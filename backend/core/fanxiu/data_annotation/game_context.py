"""凡修游戏交互上下文：识别、导航、观测、点击与控件操作。

任务通过此接口表达动作；Kernel/调度与任务编排属于执行器。上下文
接收 runner 提供的运行服务，不导入或构造具体执行器，不持有调度权。
"""
from __future__ import annotations

from .performance import counters, performance_summary

from backend.core.fanxiu.runtime_gui.scroll import (
    DEFAULT_SCROLL_SETTLE_SECONDS,
    DEFAULT_SCROLL_UNCHANGED_THRESHOLD,
)
import difflib
import hashlib
import inspect
import io
import linecache
import re
import threading
import time
from contextlib import (
    contextmanager,
    nullcontext,
)
from dataclasses import dataclass
from pathlib import Path
from types import GeneratorType
from typing import (
    Any,
    Callable,
    Iterable,
    Literal,
    Mapping,
    NoReturn,
    Sequence,
)
from pyxllib.prog import BehaviorTreeStatus
from backend.core.fanxiu.data_annotation.maintenance import is_maintenance_scene_id
from backend.core.fanxiu.data_annotation.task_context import mark_scheduler_next_time_written
from backend.core.fanxiu.data_annotation.scene_diagnostics import (
    render_scene_comparison as _render_scene_comparison,
    render_unknown_scene_overview as _render_unknown_scene_overview,
    save_scene_diagnostic_frame,
)
from backend.core.fanxiu.data_annotation.scene_escalation import (
    SceneRepairRequired,
    scene_repair_guidance,
    format_scene_repair_guidance,
    escalate_persistent_scene_unknown,
    escalate_scene_repair_required,
)
from backend.core.fanxiu.data_annotation.ocr_spatial import (
    DEFAULT_TEXT_TOKEN_GAP_HEIGHT_RATIO,
    OcrTextMatch,
    find_fuzzy_text_matches,
    find_text_matches,
    group_ocr_tokens,
    locate_text_box,
    query_spatial_ocr,
    select_text_match,
    select_fuzzy_text_match,
)
from backend.core.fanxiu.data_annotation.ocr_values import (
    parse_ocr_values,
    retry_numeric_ocr,
)
from backend.core.fanxiu.runtime_gui import ocr_name_similarity
from backend.core.fanxiu.data_annotation.slider_control import (
    BalancedPointState,
    DiscreteSliderScale,
    find_labeled_percentage,
)
from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.behavior_tree.errors import SceneClickMismatch
from pyxllib.autogui import (
    ActionPlanner,
    AutomationContext,
    Shape,
    View,
)
from backend.core.fanxiu.data_annotation.ocr_values import FULLWIDTH_DIGIT_TRANSLATION
from backend.core.fanxiu.data_annotation.tasks.xianqiao_trial_actions import XianqiaoTrialActions


DEFAULT_SCROLL_RATIO = 0.5


DEFAULT_SCROLL_DURATION_SECONDS = 1.5


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
    check: Callable[["BehaviorTreeContext", str], _FanxiuWaitResult]


@dataclass(frozen=True)
class _SceneGraphRecognition:
    scene_id: int | None
    score: float
    status: str
    matched_layer: int | None = None


class SceneMatch(int):
    """A scene id carrying the facts produced by the recognizer.

    It remains an ``int`` so common control-flow code can compare, index and
    serialize it as a scene id. New code should use the explicit attributes
    when the distinction matters.
    """

    def __new__(
        cls,
        scene_id: int,
        *,
        score: float,
        matched_layer: int,
        scope: Literal["business", "global"],
        status: str,
        frame_data_url: str | None = None,
        evidence_frame_path: str | None = None,
    ) -> "SceneMatch":
        value = int.__new__(cls, int(scene_id))
        value.score = float(score or 0.0)
        value.matched_layer = int(matched_layer)
        value.scope = scope
        value.status = str(status or "matched")
        value.frame_data_url = frame_data_url
        value.evidence_frame_path = evidence_frame_path
        return value

    @property
    def scene_id(self) -> int:
        return int(self)

    @property
    def id(self) -> int:
        return int(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_id": self.scene_id,
            "score": self.score,
            "matched_layer": self.matched_layer,
            "scope": self.scope,
            "status": self.status,
        }


class SceneWaitTimeout(TimeoutError):
    """The normal wait and all-layer guard ended without a recognized scene."""

    def __init__(
        self,
        message: str,
        *,
        expected_scene_ids: Iterable[int],
        last_match: SceneMatch | None,
        evidence_frame_path: str | None = None,
        frame_data_url: str | None = None,
        codex_dispatch_id: str | None = None,
        codex_request_path: str | None = None,
        codex_escalation_error: str | None = None,
    ) -> None:
        self.expected_scene_ids = tuple(int(scene_id) for scene_id in expected_scene_ids)
        self.repair_guidance = scene_repair_guidance(
            scene_id=None, evidence_frame_path=evidence_frame_path,
            expected_scene_ids=self.expected_scene_ids,
        )
        super().__init__(message + format_scene_repair_guidance(self.repair_guidance))
        self.last_match = last_match
        self.evidence_frame_path = evidence_frame_path
        self.frame_data_url = frame_data_url
        self.codex_dispatch_id = codex_dispatch_id
        self.codex_request_path = codex_request_path
        self.codex_escalation_error = codex_escalation_error


class BehaviorTreeContext(XianqiaoTrialActions, AutomationContext):
    """凡修行为树单次 Cell 执行上下文。

    业务层只感知执行上下文；ctx、当前帧、资产树路径和底层点击/匹配实现都收敛在这里。
    """

    default_wait_click_timeout = 18.0
    default_wait_condition_timeout = 12.0
    scene_unmatched_guard_seconds = 120.0
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
        self.last_clicked_at: float = 0.0
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
        mark_scheduler_next_time_written(task_id)

    def set_job_next_time(self, task_id: str, next_time: str | None) -> None:
        """Apply an authorized external fact directly to another Job trigger."""

        self.runner._persist_scheduler_task_next_time(task_id, next_time)

    def performance_summary(self):
        """Read current Cell counters; phases overlap and are not additive."""
        return performance_summary(self.ctx)

    def cur_frame(self, update: bool = False) -> str:
        if update:
            self.clear_frame()
        if isinstance(self.frame_data_url, str) and self.frame_data_url:
            self.runner._set_tick_frame(self.ctx, self.frame_data_url)
            return self.frame_data_url
        self.frame_data_url = self.runner._screencap(self.ctx)
        return self.frame_data_url

    def _recognize_scene_layers(
        self,
        layer0: Iterable[View | int] | View | int | None = None,
        wait: float = 5.0,
    ):
        """Run the layered recognition engine used by :meth:`wait_scene`.

        ``layer0`` is the optional business-context candidate set and ``wait``
        is its fresh-frame polling budget. Popup candidates always compete in
        the same Layer-0 graph. Once the Layer-0 budget is exhausted, the same
        fact frame falls through to Layer 1 and then Layer 2. With no business
        Layer 0, ``wait`` instead bounds fresh-frame retries while the global
        layers remain unknown.

        The method is a behavior-tree generator because handling a popup must
        return control to the next tree tick before a new observation is read.
        It returns ``(match, score, frame_data_url)``. ``match`` is a
        :class:`SceneMatch` containing the actual recognition layer, or
        ``None`` when the frame remains unresolved.
        """

        wait_seconds = float(wait or 0.0)
        if wait_seconds < 0.0:
            raise ValueError("wait_scene wait 必须大于等于 0")

        business_ids: list[int] = []
        if layer0 is not None:
            layer0_items = (
                [layer0]
                if isinstance(layer0, (View, int))
                else list(layer0)
            )
            business_ids = [
                item.id if isinstance(item, View) else int(item)
                for item in layer0_items
            ]
            business_ids = list(dict.fromkeys([
                *(int(scene_id) for scene_id in business_ids if scene_id is not None),
                *self.active_business_view_ids(),
            ]))
        business_id_set = set(business_ids)

        popup_candidates = self.popup_candidates()
        popup_by_scene_id = {
            int(scene_id): candidate
            for candidate in popup_candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("image"), dict)
            and (scene_id := self.runner._image_number(candidate["image"])) is not None
        }
        layer0_ids = list(dict.fromkeys([*business_ids, *popup_by_scene_id]))
        start = time.monotonic()
        handled_popup_ids: list[int] = []
        handled_exit_signatures: list[tuple[int, str]] = []

        def commit(
            recognition: _SceneGraphRecognition,
            frame: str,
            *,
            scope: Literal["business", "global"],
        ) -> tuple[SceneMatch | None, float, str]:
            normalized_score = float(recognition.score or 0.0)
            if not self.ctx.get("_fanxiu_scene_observation_probe"):
                self.runner._commit_scene_observation(
                    self.ctx,
                    frame,
                    recognition.scene_id,
                    normalized_score,
                )
            match = (
                SceneMatch(
                    recognition.scene_id,
                    score=normalized_score,
                    matched_layer=recognition.matched_layer,
                    scope=scope,
                    status=recognition.status,
                    frame_data_url=frame,
                )
                if recognition.scene_id is not None and recognition.matched_layer is not None
                else None
            )
            return match, normalized_score, frame

        def handle_popup(recognition: _SceneGraphRecognition, frame: str) -> bool:
            """弹窗归属与命中层无关；全局兜底命中也必须执行同一处理动作。"""
            scene_id = recognition.scene_id
            if is_maintenance_scene_id(scene_id):
                # 维护/停更页落在“弹窗”分组但没有可点击出口：它是要锁定的全局
                # 不可用事实，不能退化成“弹窗缺少动作”的资产报修。
                self.runner._raise_game_maintenance(
                    scene_id=int(scene_id),
                    evidence={
                        "stage": "maintenance_popup",
                        "recognized_scene_id": int(scene_id),
                    },
                )
            if scene_id not in popup_by_scene_id:
                return False
            if (scene_id in business_id_set
                    and scene_id not in self.runner._LEAVE_CONFIRM_VIEW_IDS):
                return False
            from .popup_recovery import (
                VerifiedPopupExitStalled, exit_shape_signature, verified_exit_recovery,
            )
            exit_shape = popup_by_scene_id[int(scene_id)].get("action_shape")
            exit_view = popup_by_scene_id[int(scene_id)]["image"]
            exit_key = (int(scene_id), exit_shape_signature(exit_shape)) if exit_shape else None
            if (verified_exit_recovery(exit_shape, scene_id=int(scene_id),
                                       frame_size=(exit_view.get("width"), exit_view.get("height")))
                    and len(handled_exit_signatures) >= 3
                    and handled_exit_signatures[-3:] == [exit_key] * 3):
                evidence = save_scene_diagnostic_frame(
                    self.runner, frame, kind="scene_repair", label="verified_exit_stalled",
                    expected_scene_ids=business_ids, matched_scene_id=int(scene_id),
                )
                error = VerifiedPopupExitStalled(
                    f"已核验退出 Shape 连续 3 次点击后仍为 #{scene_id}；原始帧={evidence}"
                )
                self.runner._scene_repair_error = error
                raise error
            if len(handled_popup_ids) >= 9:
                sequence = " -> ".join(f"#{item}" for item in handled_popup_ids)
                self.require_scene_repair(
                    int(scene_id), frame,
                    reason=f"场景识别连续处理弹窗超过上限：{sequence or 'unknown'}",
                    expected_scene_ids=business_ids,
                )
            if not self.runner._handle_recognized_popup_candidate(
                self, popup_by_scene_id[int(scene_id)],
                score=float(recognition.score or 0.0),
                expected_scene_ids=business_id_set,
            ):
                self.require_scene_repair(
                    int(scene_id), frame,
                    reason=f"场景识别命中弹窗 #{scene_id}，但该节点没有可执行的中断处理动作",
                    expected_scene_ids=business_ids,
                )
            handled_popup_ids.append(int(scene_id))
            actual_shape = getattr(self, "last_clicked_shape", None)
            handled_exit_signatures.append((int(scene_id), exit_shape_signature(actual_shape.raw))
                                           if isinstance(actual_shape, Shape) else (int(scene_id), ""))
            return True

        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.clear_frame()
            yield BehaviorTreeStatus.RUNNING
            frame = self.cur_frame(update=True)
            elapsed = time.monotonic() - start

            # This notice can overlay a login cover whose loose world identity
            # also matches. Observe it before assigning any business owner.
            from .external_login_handoff import FanxiuExternalLoginWait
            handoff = self.observe_external_login_notice(frame_data_url=frame)
            if handoff:
                if handoff["blocked"]:
                    raise FanxiuExternalLoginWait(handoff)
                # Only an executing formal Job may reconnect after expiry.
                # Read-only probes never dismiss the human operator's notice.
                if self.payload.get("__scheduler_task_id") and not self.ctx.get("_fanxiu_scene_observation_probe"):
                    self.click_shape_center(909, "确定")
                    yield from self.wait_action_settle(1.5)
                    if self.observe_external_login_notice():
                        raise RuntimeError("账号交接提示确认后仍可见，保留现场")
                    from .external_login_handoff import complete_external_login_handoff
                    complete_external_login_handoff()
                    start = time.monotonic()
                    continue
                raise RuntimeError("账号交接等待已届满，有到期作业时才确认登录")

            layer0_recognition = _SceneGraphRecognition(None, 0.0, "no_match")
            if layer0_ids:
                with self.runner._scene_observation_probe(self.ctx):
                    layer0_recognition = self.runner._identify_scene_number_by_graph(
                        self.ctx,
                        frame,
                        layer0_ids,
                    )
            layer0_scene_id = layer0_recognition.scene_id

            if is_maintenance_scene_id(layer0_scene_id):
                self.runner._raise_game_maintenance(
                    scene_id=int(layer0_scene_id),
                    evidence={
                        "stage": "maintenance_scene",
                        "recognized_scene_id": int(layer0_scene_id),
                    },
                )
            # One graph result, one owner. Leave confirmations have a single
            # global owner even when old assets declare them as destinations.
            # Other explicitly requested popup scenes remain business-owned.
            if (layer0_scene_id in business_id_set
                    and layer0_scene_id not in self.runner._LEAVE_CONFIRM_VIEW_IDS):
                return commit(layer0_recognition, frame, scope="business")
            if handle_popup(layer0_recognition, frame):
                # Closing animations can retain the old identity for >1s.
                # Do not send a second background/Return click through the
                # fading popup into the underlying business page.
                yield from self.wait_action_settle(1.5)
                start = time.monotonic()  # 弹窗动作后的新过渡重新享有等待预算。
                continue
            has_business_layer0 = layer0 is not None and bool(business_ids)
            if has_business_layer0 and elapsed < wait_seconds:
                continue

            with self.runner._scene_observation_probe(self.ctx):
                global_recognition = self.runner._identify_scene_number_by_graph(
                    self.ctx,
                    frame,
                    include_default_popup_candidates=False,
                )
            if handle_popup(global_recognition, frame):
                yield from self.wait_action_settle(1.5)
                # 与 Layer 0 相同：处理后下一 tick 重新观察原业务候选。
                start = time.monotonic()
                continue
            if global_recognition.scene_id is not None:
                return commit(global_recognition, frame, scope="global")
            if not has_business_layer0 and elapsed < wait_seconds:
                continue
            return commit(global_recognition, frame, scope="global")

    def recognize_scene_in_frame(
        self,
        views: Iterable[View | int] | None = None,
        *,
        frame_data_url: str,
    ) -> tuple[int | None, float, str]:
        """Classify an already captured frame without operating the game.

        This is a pure same-frame analysis primitive: callers must supply the
        frame and it never captures a new one, waits, handles a popup, or
        clicks.  Any live scene observation must enter through ``wait_scene``
        (or its ``current_scene`` wrapper) before using this helper.
        """

        if not isinstance(frame_data_url, str) or not frame_data_url:
            raise ValueError("recognize_scene_in_frame 必须传入已有 frame_data_url")
        frame = frame_data_url
        business_view_ids: list[int] = []
        if views is not None:
            business_view_ids = [view.id if isinstance(view, View) else int(view) for view in views]
            business_view_ids = [view_id for view_id in business_view_ids if view_id is not None]
            business_view_ids = list(dict.fromkeys([
                *(int(scene_id) for scene_id in business_view_ids),
                *self.active_business_view_ids(),
            ]))
        popup_candidates = self.popup_candidates()
        popup_by_scene_id = {
            int(scene_id): candidate
            for candidate in popup_candidates
            if isinstance(candidate, dict)
            and isinstance(candidate.get("image"), dict)
            and (scene_id := self.runner._image_number(candidate["image"])) is not None
        }
        layer0_ids = list(dict.fromkeys([*business_view_ids, *popup_by_scene_id]))
        scene_id: int | None = None
        score = 0.0
        if layer0_ids:
            with self.runner._scene_observation_probe(self.ctx):
                scene_id, score = self.runner._identify_scene_number(
                    self.ctx,
                    frame,
                    layer0_ids,
                )
        if scene_id is None:
            with self.runner._scene_observation_probe(self.ctx):
                scene_id, score = self.runner._identify_scene_number(
                    self.ctx,
                    frame,
                    include_default_popup_candidates=False,
                )
        if scene_id == 546:
            self.runner._raise_game_maintenance(
                scene_id=546,
                evidence={"stage": "maintenance_scene", "recognized_scene_id": 546},
            )
        normalized_score = float(score or 0.0)
        if not self.ctx.get("_fanxiu_scene_observation_probe"):
            self.runner._commit_scene_observation(
                self.ctx,
                frame,
                scene_id,
                normalized_score,
            )
        return scene_id, normalized_score, frame

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

    def _shape_path(self, shape: Shape) -> str:
        parts: list[str] = []
        current: Shape | None = shape
        while isinstance(current, Shape):
            parts.append(str(current.title or current.raw.get("id") or "<shape>"))
            current = current.parent_shape
        return "[" + "/".join(reversed(parts)) + "]"

    def resolve_shape_selector(self, view: View, selector: Shape | str) -> Shape:
        if isinstance(selector, Shape):
            return selector
        from .shape_selectors import resolve_shape_path
        return resolve_shape_path(view, str(selector))

    def _shape_match_search_shape(self, shape: Shape) -> dict[str, Any]:
        raw = dict(shape.raw)
        parent = shape.parent_shape
        if not isinstance(parent, Shape):
            return raw
        parent_raw = parent.raw
        # OCR floating controls must obey the same parent search envelope as
        # image controls; otherwise a right-menu action searches the full page.
        scan_box = {key: parent_raw.get(key) for key in ("x", "y", "w", "h") if key in parent_raw}
        if {"x", "y", "w", "h"}.issubset(scan_box):
            raw["_match_scan_box"] = scan_box
        if self.runner._shape_image_role(raw) != "off":
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

    def _execution_source_info(self, action: str, source_expr: str = "") -> dict[str, Any]:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back
        own_file = Path(__file__).resolve()
        # Both the interaction facade and its execution adapter are framework
        # frames. Preserve the first business caller after the module split.
        framework_files = {own_file, own_file.with_name("behavior_tree_executor.py")}
        while frame is not None:
            filename = Path(frame.f_code.co_filename).resolve()
            if filename not in framework_files:
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
                    "source_expr": source_expr or self._compact_execution_source_expr(line),
                }
            frame = frame.f_back
        return {"action": action, "source_expr": source_expr}

    def _compact_execution_source_expr(self, line: str) -> str:
        text = str(line or "").strip()
        text = re.sub(r"^.+?=\s*yield\s+from\s+", "", text)
        text = re.sub(r"^yield\s+from\s+", "", text)
        return text.replace("context.", "").strip()

    def _format_execution_call(self, name: str, *args: Any) -> str:
        return f"{name}({', '.join(self._format_execution_arg(arg) for arg in args)})"

    def _format_execution_arg(self, value: Any) -> str:
        if isinstance(value, View):
            return str(value.id) if value.id is not None else repr(value.title)
        if isinstance(value, Shape):
            return repr(value.title or value.raw.get("id") or "shape")
        return repr(value)

    def _emit_execution_action(
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
        """Guard the source scene, locate the Shape, then click exactly once.

        The mandatory Layer-0 ``wait_scene`` guard runs first and its fresh,
        popup-free frame is passed to the Shape wait as an optional first-round
        observation. The executor reuses it only while it stays the live frame
        for the same source scene and within a strict freshness bound; any miss
        then falls back to the original full fresh guard, so popup protection
        and click authorization are unchanged.
        """
        source_info = options.pop("_source_info", None)
        if not isinstance(source_info, dict):
            source_info = self._execution_source_info("wait_click", self._format_execution_call("wait_click", frame, shape))
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
        target_view = target.parent_view if isinstance(target.parent_view, View) and isinstance(target.parent_view.raw, dict) else view
        label = f"wait_click #{view.id or '?'} {self._shape_path(target)}"
        # Every game action re-enters the mandatory scene pipeline.  The
        # caller's expected source view never owns an overlapping popup ID;
        # Layer 0 must clear interruptions before this click may proceed.
        guarded_match = yield from self.wait_scene(
            [view],
            wait=timeout,
            label=f"{label}：点击前守护",
        )
        guarded_scene_id = int(getattr(guarded_match, "id", guarded_match))
        if guarded_scene_id != int(view.id):
            raise SceneClickMismatch(
                f"{label}：点击前场景为 #{guarded_scene_id}，不是预期 #{view.id}，拒绝点击",
                expected_scene_id=int(view.id),
                actual_scene_id=guarded_scene_id,
            )
        # Layer 0 已经产出干净、通过弹窗仲裁的源场景帧。把它作为可选首轮观测
        # 交给 Shape 等待，使同一次点击事务不再重复完整场景识别；注入端只在
        # 帧仍是上下文当前帧、源场景一致且足够新鲜时才会复用。
        guarded_observation: tuple[str, int] | None = None
        guarded_frame = getattr(guarded_match, "frame_data_url", None)
        if isinstance(guarded_frame, str) and guarded_frame:
            guarded_observation = (guarded_frame, guarded_scene_id)
        self._emit_execution_action(
            f"点击 #{view.id or '?'}「{self._shape_path(target)}」",
            phase="execution_wait_click",
            kind="waitClick",
            source_info=source_info,
            current_scene=view.id,
        )
        if self.runner._shape_has_click_condition(target.raw):
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
                initial_observation=guarded_observation,
            )
            click_options: dict[str, float] = {}
            if x_ratio != 0.5 or y_ratio != 0.5:
                click_options.update(x_ratio=x_ratio, y_ratio=y_ratio)
            # Publish action intent only after Layer 0 has accepted the source
            # scene and immediately before the real click.  Publishing it
            # earlier could authorize a popup left over from a previous action.
            self.last_clicked_shape = target
            self.last_clicked_at = time.monotonic()
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
        self.last_clicked_shape = target
        self.last_clicked_at = time.monotonic()
        self.runner._click_frame_point(self.ctx, target_view.raw, click_x, click_y)
        self.clear_frame()

    def shape_matches(
        self,
        frame: View | int | str | None,
        shape: Shape | str,
        *,
        frame_data_url: str | None = None,
        require_unique: bool = True,
    ) -> dict[str, Any] | None:
        """Read one fresh frame against a Shape's declared visual conditions.

        This is intentionally a no-click primitive.  It lets a Job distinguish
        a stateful visual gate (for example a bright, unclaimed reward mask)
        from a fixed-coordinate action before consuming it.  Unconstrained
        shapes cannot be used as state evidence.

        ``require_unique`` defaults to True so every existing click gate keeps
        the strict "one unambiguous floating object" contract.  Pass False only
        for a deliberate enumeration read (for example a list of identical red
        packet badges): the raw multi-match result is preserved and candidate
        validity is judged per ``crop_similarity`` instead of collapsing to a
        single object or a zero similarity.
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
        if not self.runner._shape_has_click_condition(target.raw):
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
                require_unique=require_unique,
            )
            if bool(result.get("matched")):
                return result
        return None

    def wait_clicks(self, steps: Iterable[tuple[View | int | str | None, Shape | str]]):
        for frame, shape in steps:
            source_info = self._execution_source_info("wait_click", self._format_execution_call("wait_click", frame, shape))
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
                _wait_scene_match = yield from self.wait_scene([source_view], wait=5.0, required=False)
                (scene_id, score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
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

    def wait_click_then_scene(
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
        """Click ``shape`` on ``frame`` then wait for one exact declared target.

        The post-click wait is :meth:`wait_scene_exact`: only the resolved
        target ids count as success, so a persistent source scene can no longer
        be mistaken for the destination. The source scene and the Shape's
        legally declared landings are passed as Layer-0 observation candidates
        so they are recognized cheaply instead of falling to global L2. A
        limited re-click only happens after the wait budget expires and a fresh
        observation still confirms the source scene.
        """
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
        # 成功条件只有 target_ids；源场景与 Shape 合法声明落点仅作为 Layer-0
        # 观测候选，使其能在廉价层被识别，而不会被误当成功或在全局 L2 反复重算。
        observation_ids: list[int] = []
        if source_view.id is not None:
            observation_ids.append(int(source_view.id))
        observation_ids.extend(int(target_id) for target_id in declared_target_ids)
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
                            return (yield from self.wait_leave_scene(
                                source_view,
                                timeout=wait_timeout,
                                label=label or f"点击后等待离开 #{source_view.id or '?'}",
                            ))
                        target_view = yield from self.wait_scene_exact(
                            target_ids,
                            timeout=wait_timeout,
                            label=wait_label,
                            observation_scenes=observation_ids,
                        )
                        self._record_wait_click_then_scene_landing(source_view, target_shape, target_view)
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
                        _wait_scene_match = yield from self.wait_scene(wait=5.0, required=False)
                        (scene_id, score, _frame) = (
                            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
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
        """Move the SDK bubble to the proven left dock and require #590.

        The SDK auto-collapses its bubble after three seconds.  In that known
        state the first click only expands it and the second opens the menu.
        Deliberately wait for that state before the two-click transaction so
        an already-expanded bubble can never receive a second click through
        the popup.
        """
        attempt_timeout = max(2.0, min(8.0, float(timeout)))
        # The mandatory scene guard owns reward-overlay dismissal. Calling
        # wait_click(421, "关闭") after observing the overlay runs that guard
        # again: it may already return to #34, making the explicit click stale.
        # Establish the post-dismissal world state once, then locate the SDK.
        world = yield from self.wait_scene(
            [34], wait=attempt_timeout, label="气泡入口：清理干扰并确认世界页"
        )
        if int(getattr(world, "id", world)) != 34:
            raise RuntimeError("气泡入口：清理干扰后未确认世界页，拒绝操作悬浮球")

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

        # Wait beyond SqBaseFloatView.STAY_EDGE_DELAY_MILLIS (3000 ms), then
        # re-establish the live bubble geometry before clicking anything.
        yield from self.wait_action_settle(3.2)
        collapsed_frame = self.cur_frame(update=True)
        collapsed_match = self.shape_matches(421, "气泡", frame_data_url=collapsed_frame)
        collapsed_box = (
            (collapsed_match or {}).get("resolved_box")
            or (collapsed_match or {}).get("fixed_box")
        )
        if not isinstance(collapsed_box, dict) or not bool(
            (collapsed_match or {}).get("unique_match", True)
        ):
            raise RuntimeError(
                "气泡入口：等待半隐藏态后悬浮球未唯一定位，拒绝点击"
            )
        collapsed_x = float(collapsed_box.get("x") or 0) + float(collapsed_box.get("w") or 0) / 2
        collapsed_y = float(collapsed_box.get("y") or 0) + float(collapsed_box.get("h") or 0) / 2
        if abs(collapsed_x - target_x) > 80.0 or abs(collapsed_y - target_y) > 80.0:
            raise RuntimeError(
                "气泡入口：半隐藏悬浮球已离开安全区，拒绝点击"
            )
        self.runner._log(
            "action",
            f"气泡入口：半隐藏态首击展开 ({collapsed_x:.0f},{collapsed_y:.0f})",
        )
        self.click_frame_point(421, collapsed_x, collapsed_y)
        yield from self.wait_action_settle(0.45)
        self.runner._log(
            "action",
            f"气泡入口：在 3 秒窗口内第二击打开菜单 ({collapsed_x:.0f},{collapsed_y:.0f})",
        )
        self.click_frame_point(421, collapsed_x, collapsed_y)
        yield from self.wait_action_settle(settle_seconds)
        try:
            return (yield from self.wait_scene(
                [590],
                wait=attempt_timeout,
                label="安全区点击气泡后等待37手游弹窗#590",
            ))
        except TimeoutError as exc:
            matched, _score, _frame = self.match_view(590, update=True)
            if matched:
                return self.view(590)
            raise TimeoutError("安全区悬浮球单击后未打开 #590；禁止原地重试") from exc

    def _record_wait_click_then_scene_landing(
        self,
        source_view: View,
        shape: Shape,
        target: SceneMatch | View | int,
    ) -> None:
        target_scene_id = getattr(target, "scene_id", getattr(target, "id", target))
        if target_scene_id is None:
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
            int(target_scene_id),
            reason=f"#{source_view.id or '?'} 点击后等待目标场景",
        )

    def wait_leave_scene(
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
        source_info = self._execution_source_info("wait_leave_scene", self._format_execution_call("wait_leave_scene", source_id))
        self._emit_execution_action(
            f"{label}：等待离开 #{source_id}",
            phase="execution_wait_leave_scene",
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
            _wait_scene_match = yield from self.wait_scene(wait=5.0, required=False)
            (scene_id, score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
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

    def go_scene(
        self,
        scene: View | int,
        *,
        wait: float | None = None,
        known_paths_only: bool = False,
    ) -> Any:
        """Navigate using asset edges; reuse action landings as Layer-0 hints.

        Each hint is checked on a fresh frame; a miss keeps the normal layered
        fallback. known_paths_only disables unknown-route exploration, without
        changing recognition, popup handling or replanning of known edges.
        """
        target_scene_id = scene.id if isinstance(scene, View) else int(scene)
        if not isinstance(self.asset_tree_path, Path):
            raise RuntimeError("缺少场景移动资产树路径")
        self._emit_execution_action(
            f"前往 #{target_scene_id}",
            phase="execution_go_scene",
            kind="goto",
            source_info=self._execution_source_info("go_scene", self._format_execution_call("go_scene", target_scene_id)),
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
            go_scene_kwargs["layer0_wait_seconds"] = wait
        hint = self.ctx.pop("_navigation_observation", None)
        if (hint and time.monotonic() - hint[2] <= 5.0
                and hint[1] == self.ctx.get("_tick_frame_data_url")
                and "initial_scene_id" in go_scene_parameters):
            # A hint only orders new-frame candidates; never authorizes a click.
            go_scene_kwargs["initial_scene_id"] = hint[0]
        if known_paths_only:
            go_scene_kwargs["known_paths_only"] = True
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

    def wait_scene_exact(
        self,
        scenes: Iterable[View | int],
        *,
        timeout: float,
        label: str = "等待目标场景",
        observation_scenes: Iterable[View | int] | None = None,
    ):
        """等待 targets 中的任一落点并返回 SceneMatch，其他识别只作为过渡事实。

        与 wait_scene 的“任意层返回”语义不同，``scenes``（targets）始终是唯一
        成功条件，返回值保证属于 targets。底层仍执行完整分层识别和弹窗处理；
        每轮使用新帧，不点击导航动作。timeout 是重观测预算，在完整识别之间
        检查，不能中断正在进行的 OCR；预算耗尽后抛 TimeoutError，包含最后结果。

        ``observation_scenes``（observed）只扩充 Layer 0 业务候选，不参与成功判定。
        把已知源场景、Shape 合法声明落点等一并作为 Layer-0 候选，可让每轮在廉价
        的 Layer 0 直接识别它们，而不是掉到代价高昂的全局 L2；匹配到源场景不会
        成功。已声明过渡候选时，Layer 0 使用剩余等待预算，避免加载途中每隔
        5 秒提前扩大全局搜索；预算末尾仍保留完整分层诊断。
        """
        targets = tuple(dict.fromkeys(
            scene.id if isinstance(scene, View) else int(scene) for scene in scenes
        ))
        if not targets or any(scene_id is None for scene_id in targets):
            raise ValueError("wait_scene_exact scenes 必须包含有效场景编号")
        observed = tuple(dict.fromkeys(
            scene.id if isinstance(scene, View) else int(scene)
            for scene in (observation_scenes or ())
        ))
        candidate_ids = tuple(dict.fromkeys([*targets, *observed]))
        budget = float(timeout)
        if budget < 0:
            raise ValueError("wait_scene_exact timeout 必须大于等于 0")
        deadline = time.monotonic() + budget
        expected = "/".join(f"#{scene_id}" for scene_id in targets)
        last_match = None
        while True:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            self.clear_frame()
            remaining = max(0.0, deadline - time.monotonic())
            layer0_wait = remaining if observed else min(remaining, 5.0)
            last_match = yield from self.wait_scene(
                candidate_ids,
                wait=layer0_wait,
                required=False,
                label=label,
            )
            if last_match is not None and last_match.scene_id in targets:
                return last_match
            if time.monotonic() >= deadline:
                actual = f"#{last_match.scene_id} {last_match.score:.0f}%" if last_match is not None else "unknown"
                raise TimeoutError(f"{label} 超时，未检测到 {expected}，最后 {actual}")
            yield from self.wait_action_settle(min(0.5, max(0.0, deadline - time.monotonic())))

    def wait_scene(
        self,
        scenes: Iterable[View | int] | None = None,
        wait: float = 5.0,
        *,
        required: bool = True,
        label: str = "等待场景",
    ):
        """Return the first scene recognized by the complete layered flow.

        ``scenes`` is the business Layer-0 candidate collection. ``wait`` is
        only the Layer-0 fresh-frame budget; after it expires, the same frame
        falls through Layer 1 and Layer 2. Any formal-layer match is returned
        as :class:`SceneMatch`. If every layer misses, the complete flow is
        retried from behavior-tree ticks for the fixed unmatched guard before
        a :class:`SceneWaitTimeout` is raised.
        """

        view_ids = [
            scene.id if isinstance(scene, View) else int(scene)
            for scene in (scenes or ())
        ]
        view_ids = [view_id for view_id in view_ids if view_id is not None]
        if scenes is not None and not view_ids:
            raise ValueError("wait_scene scenes 不能为空")
        wait_timeout = float(wait)
        if wait_timeout < 0.0:
            raise ValueError("wait_scene wait 必须大于等于 0")
        source_info = self._execution_source_info("wait_scene", self._format_execution_call("wait_scene", *view_ids))
        expected_label = "/".join(f"#{view_id}" for view_id in view_ids) or "全局场景"
        self._emit_execution_action(
            f"{label}：等待 {expected_label}",
            phase="execution_wait_scene",
            kind="wait",
            source_info=source_info,
            current_scene=view_ids[0] if len(view_ids) == 1 else None,
        )
        def finish(match: SceneMatch, score: float) -> SceneMatch:
            self.ctx["_navigation_observation"] = (match.scene_id, match.frame_data_url, time.monotonic())
            if match.scope == "global" and match.matched_layer == 2 and not match.evidence_frame_path:
                match.evidence_frame_path = save_scene_diagnostic_frame(
                    self.runner,
                    match.frame_data_url or "",
                    kind="layer2_match",
                    label=label,
                    expected_scene_ids=view_ids,
                    matched_scene_id=match.scene_id,
                    matched_layer=match.matched_layer,
                )
            with self.runner._lock:
                self.runner._status.update({
                    "current_scene": match.scene_id,
                    "updated_at": time.time(),
                })
            self._record_pending_declared_click_landing(match.scene_id)
            self.runner._log(
                "success",
                f"{label}：识别为 #{match.scene_id} Layer{match.matched_layer} {score:.0f}%",
            )
            return match

        timing_before = counters(self.ctx).snapshot()
        identify_started_at = time.monotonic()
        match, score, frame = yield from self._recognize_scene_layers(
            view_ids if scenes is not None else None,
            wait=wait_timeout,
        )
        identify_elapsed = time.monotonic() - identify_started_at
        counters(self.ctx).record("scene_wait", identify_elapsed)
        if identify_elapsed >= 1.0:
            self.runner._log(
                "detail",
                f"{label}：分层识别耗时 {identify_elapsed:.2f}s，结果 "
                f"{'#' + str(match.scene_id) if match is not None else 'unknown'} {score:.0f}% "
                f"阶段统计（嵌套不可相加）={performance_summary(self.ctx, since=timing_before)}",
            )
        if match is not None:
            return finish(match, score)
        if not required:
            return None

        guard_started_at = time.monotonic()
        guard_seconds = max(0.0, float(self.scene_unmatched_guard_seconds))
        while time.monotonic() - guard_started_at < guard_seconds:
            if self.stop_event is not None:
                self.runner._raise_if_stopped(self.stop_event)
            with self.runner._lock:
                self.runner._status.update({
                    "phase": "wait_scene",
                    "current_scene": None,
                    "message": f"{label}：全层未匹配，保底重试中",
                    "updated_at": time.time(),
                })
            match, score, frame = yield from self._recognize_scene_layers(
                view_ids if scenes is not None else None,
                wait=0.0,
            )
            if match is not None:
                return finish(match, score)

        evidence_frame_path = save_scene_diagnostic_frame(
            self.runner,
            frame,
            kind="unmatched",
            label=label,
            expected_scene_ids=view_ids,
        )
        expected = "/".join(f"#{view_id}" for view_id in view_ids)
        evidence_text = f"，原始帧={evidence_frame_path}" if evidence_frame_path else ""
        codex_dispatch_id: str | None = None
        codex_request_path: str | None = None
        codex_escalation_error: str | None = None
        scheduler_task_id = str(self.payload.get("__scheduler_task_id") or "").strip()
        if scheduler_task_id:
            try:
                dispatch = escalate_persistent_scene_unknown(
                    task_id=scheduler_task_id,
                    task_label=label,
                    entry_id=str(self.ctx.get("entry_id") or ""),
                    expected_scene_ids=view_ids,
                    evidence_frame_path=evidence_frame_path,
                    asset_tree_path=self.asset_tree_path,
                    layer0_wait_seconds=wait_timeout,
                    unmatched_guard_seconds=guard_seconds,
                    attempt_id=str(self.payload.get("__scheduler_attempt_id") or ""),
                )
                if dispatch is not None:
                    codex_dispatch_id = dispatch.dispatch_id
                    codex_request_path = dispatch.request_path
                    evidence_text += f"，Codex投递={codex_dispatch_id}"
                else:
                    evidence_text += "，由当前 AI 运行权持有者处理"
            except Exception as exc:
                codex_escalation_error = f"{type(exc).__name__}: {exc}"
                evidence_text += f"，Codex投递失败={codex_escalation_error}"
        self.runner._log(
            "warning",
            f"{label}：全层持续未匹配 {guard_seconds:.0f}s{evidence_text}",
        )
        error = SceneWaitTimeout(
            f"{label} 全层持续未匹配，期望业务场景 {expected}{evidence_text}",
            expected_scene_ids=view_ids,
            last_match=None,
            evidence_frame_path=evidence_frame_path,
            frame_data_url=frame,
            codex_dispatch_id=codex_dispatch_id,
            codex_request_path=codex_request_path,
            codex_escalation_error=codex_escalation_error,
        )
        self.runner._scene_repair_error = error
        raise error

    def current_scene(
        self,
        scenes: Iterable[View | int] | None = None,
        *,
        update: bool = True,
        label: str = "识别当前场景",
    ):
        """Observe one fresh non-popup scene through the mandatory wait pipeline.

        Popup interruptions remain mandatory.  ``None`` means that every formal
        global scene may match; an all-layer miss is returned as an unknown
        observation rather than raising :class:`SceneWaitTimeout`.
        """

        if update:
            self.clear_frame()
        match = yield from self.wait_scene(
            scenes,
            wait=0.0,
            required=False,
            label=label,
        )
        if match is None:
            return None, 0.0, str(self.frame_data_url or "")
        return (
            int(match.scene_id),
            float(match.score or 0.0),
            str(match.frame_data_url or self.frame_data_url or ""),
        )

    def require_scene_repair(
        self, scene_id: int | None, frame: str, *, reason: str,
        expected_scene_ids: Iterable[int] = (),
    ) -> NoReturn:
        """Preserve this failed observation and stop rather than retry bad assets.

        Engineering Jobs hand off to an independent Agent. AI-owned Jobs and
        ordinary diagnostic Cells raise to their existing caller without spawning.
        No recapture or click is performed here.
        """
        expected_scene_ids = tuple(expected_scene_ids)
        evidence = save_scene_diagnostic_frame(
            self.runner, frame, kind="scene_repair", label=reason,
            expected_scene_ids=expected_scene_ids, matched_scene_id=scene_id,
        )
        dispatch = None
        escalation_error = None
        task_id = str(self.payload.get("__scheduler_task_id") or "").strip()
        if task_id:
            try:
                dispatch = escalate_scene_repair_required(
                    task_id=task_id, task_label=reason,
                    entry_id=str(self.ctx.get("entry_id") or ""), scene_id=scene_id,
                    expected_scene_ids=expected_scene_ids, evidence_frame_path=evidence,
                    asset_tree_path=self.asset_tree_path, reason=reason,
                    attempt_id=str(self.payload.get("__scheduler_attempt_id") or ""),
                )
            except Exception as exc:
                escalation_error = f"{type(exc).__name__}: {exc}"
        detail = f"{reason}；原始帧={evidence}"
        if dispatch is not None:
            detail += f"；Codex投递={dispatch.dispatch_id}"
        elif escalation_error:
            detail += f"；自动升级失败={escalation_error}"
        else:
            detail += "；由当前调用方/AI 处理"
        self.runner._log("error", detail)
        error = SceneRepairRequired(
            detail, scene_id=scene_id, evidence_frame_path=evidence,
            dispatch=dispatch, escalation_error=escalation_error,
        )
        self.runner._scene_repair_error = error
        raise error

    def render_scene_comparison(
        self,
        failure_frame: str | Path | bytes,
        scene_id: int,
    ) -> str:
        """Build the standard two-frame, all-Shape comparison for Agent review."""

        return _render_scene_comparison(
            self.runner,
            self.ctx,
            failure_frame,
            int(scene_id),
        )

    def render_unknown_scene_overview(
        self,
        failure_frame: str | Path | bytes,
        scenes: list[int],
        *,
        top_k: int = 2,
    ) -> str:
        """Build the Shape-free multi-candidate overview for Agent review."""

        return _render_unknown_scene_overview(
            self.runner,
            self.ctx,
            failure_frame,
            [int(scene_id) for scene_id in scenes],
            top_k=top_k,
        )

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
        self._record_wait_click_then_scene_landing(source_view, clicked_shape, self.view(int(target_scene_id)))

    def scene_visible(self, view: View | int, *, threshold: float = 80.0) -> _FanxiuWaitCondition:
        target = view if isinstance(view, View) else self.get_view(int(view))
        if not isinstance(target, View):
            raise RuntimeError(f"无法解析等待场景：{view}")
        view_id = target.id
        label = f"#{view_id}「{target.title}」" if view_id is not None else f"View「{target.title}」"

        def check(context: "BehaviorTreeContext", frame: str) -> _FanxiuWaitResult:
            if target.is_match(context):
                return _FanxiuWaitResult(True, f"{label} View 已匹配", 100.0, view_id)
            scene_id, score = context.runner._identify_scene_number(context.ctx, frame, [int(view_id)] if view_id is not None else None)
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

        def check(context: "BehaviorTreeContext", frame: str) -> _FanxiuWaitResult:
            score = float(context.runner._shape_score(context.ctx, target_view.raw, target_shape.raw, frame) or 0.0)
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

        def check(context: "BehaviorTreeContext", frame: str) -> _FanxiuWaitResult:
            text = context.ocr_text(frame)
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
        def check(context: "BehaviorTreeContext", frame: str) -> _FanxiuWaitResult:
            text = context.ocr_text(frame)
            matched = bool(predicate(text))
            preview = _sanitize_ocr_text(text)[: max(0, int(preview_chars))]
            return _FanxiuWaitResult(matched, f"{label} {'命中' if matched else '未命中'}：{preview}")

        return _FanxiuWaitCondition(label=label, check=check)

    def wait_scene_or_ocr(
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
                "scene": self.scene_visible(view, threshold=view_threshold),
                "text": self.ocr_matches(predicate, label=f"{label} OCR"),
            },
            timeout=timeout,
            label=label,
        )
        target_view = view.id if isinstance(view, View) else int(view)
        _wait_scene_match = yield from self.wait_scene([target_view], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id != target_view:
            scene_id, score = target_view, 0.0
        return result, scene_id, score

    def all_of(self, *conditions: _FanxiuWaitCondition, label: str | None = None) -> _FanxiuWaitCondition:
        condition_list = [condition for condition in conditions if isinstance(condition, _FanxiuWaitCondition)]
        if not condition_list:
            raise RuntimeError("all_of 至少需要一个等待条件")
        title = label or " + ".join(condition.label for condition in condition_list)

        def check(context: "BehaviorTreeContext", frame: str) -> _FanxiuWaitResult:
            details: list[str] = []
            score: float | None = None
            current_scene: int | None = None
            for condition in condition_list:
                result = condition.check(context, frame)
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
        source_info = self._execution_source_info("wait_any", "wait_any(...)")
        self._emit_execution_action(
            f"{label}：等待 {' / '.join(str(key) for key in conditions.keys())}",
            phase="execution_wait_any",
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
            _scene_id, _score, frame = self.recognize_scene_in_frame([], frame_data_url=frame)
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
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
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
            self.last_clicked_at = time.monotonic()
        action_match_result = match_result
        if action_match_result is None and isinstance(target_shape, Shape):
            action_match_result = self._shape_match_results.get(id(target_shape.raw))
        frame = frame_data_url
        if not isinstance(frame, str) or not frame:
            frame = self.cur_frame() if action_match_result is not None or self.runner._shape_click_needs_frame(raw_shape) else None
        # Preserve localization failures. The obsolete fixed-coordinate fallback
        # was removed from the executor; bypassing it here would also authorize
        # input on a button that has not appeared yet or has moved.
        result = self.runner._click_shape(
            self.ctx,
            source_view.raw,
            raw_shape,
            frame,
            match_result=action_match_result,
            x_ratio=float(x_ratio),
            y_ratio=float(y_ratio),
        )
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

    def observe_external_login_notice(self, *, frame_data_url: str | None = None) -> dict | None:
        """Observe the human handoff notice and defer triggers; never click.

        OCR may spell 账号 as 帐号; the stable login message in its annotated
        ROI is authoritative. Full-frame OCR can omit this pale text entirely.
        """
        from .external_login_handoff import observe_external_login_notice
        frame = frame_data_url or self.cur_frame(update=True)
        text = self.ocr_text_in_shapes(909, ["账号别处登录正文"], frame_data_url=frame)
        if "已在别处登录" in re.sub(r"\s+", "", text):
            if not self.ctx.get("_fanxiu_scene_observation_probe"):
                self.runner._commit_scene_observation(self.ctx, frame, 909, 100.0)
            return observe_external_login_notice(evidence={"scene_id": 909, "text": text})
        return None

    def click_frame_point(self, view: View | int | str | dict[str, Any], x: float, y: float) -> Any:
        target_view = self.view(view)
        self._emit_execution_action(
            f"固定点击 #{target_view.id or '?'} ({float(x):.0f},{float(y):.0f})",
            phase="execution_click_point",
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
        self.last_clicked_shape = target_shape
        self.last_clicked_at = time.monotonic()
        self._emit_execution_action(
            f"固定点击 #{target_view.id or '?'}「{self._shape_path(target_shape)}」",
            phase="execution_click_shape",
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
        self._emit_execution_action(
            (
                f"固定长按 #{target_view.id or '?'} "
                f"({float(x):.0f},{float(y):.0f}) {duration_ms / 1000:.1f}s"
            ),
            phase="execution_long_press_point",
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

    def shape_center_in_box(
        self,
        view: View | int | str | dict[str, Any],
        shape: Shape | str | dict[str, Any],
        search_box: dict[str, float],
    ) -> tuple[float, float]:
        """Resolve one floating shape while excluding pixels outside ``search_box``.

        This is intentionally stricter than a full-frame floating match.  It is
        used by controls such as sliders where a visually identical ornament
        elsewhere on the page must never become the gesture origin.
        """

        target_view = self.view(view)
        target_shape = self.resolve_shape_selector(target_view, shape)
        frame = self.cur_frame(update=True)
        raw = self.runner._decode_frame_data_url(frame)
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
            width, height = image.size
            left = max(0, min(width, int(float(search_box.get("x") or 0))))
            top = max(0, min(height, int(float(search_box.get("y") or 0))))
            right = max(left, min(width, int(float(search_box.get("x") or 0) + float(search_box.get("w") or 0))))
            bottom = max(top, min(height, int(float(search_box.get("y") or 0) + float(search_box.get("h") or 0))))
            if right <= left or bottom <= top:
                raise RuntimeError("浮动 shape 局部搜索框无效")
            masked = Image.new("RGB", image.size)
            masked.paste(image.crop((left, top, right, bottom)), (left, top))
            buffer = io.BytesIO()
            masked.save(buffer, format="PNG")
        center = self.runner._shape_center(
            target_shape.raw,
            target_view.raw,
            self.runner._data_url(buffer.getvalue()),
            self.ctx,
            strict_live=True,
        )
        if not (left <= center[0] <= right and top <= center[1] <= bottom):
            raise RuntimeError("浮动 shape 实时中心落在局部搜索框外")
        return center

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

        _wait_scene_match = yield from self.wait_scene([source_id], wait=5.0, required=False)
        (scene_id, _score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id != source_id:
            yield from self.wait_scene(
                [source_view],
                wait=initial_timeout,
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
                _wait_scene_match = yield from self.wait_scene([source_id], wait=5.0, required=False)
                (scene_id, _score, _frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
                )
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
        self._emit_execution_action(
            f"长按 #{target_view.id or '?'}「{self._shape_path(target_shape)}」 {duration_ms / 1000:.1f}s",
            phase="execution_long_press_shape",
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

    def click_shape_center_then_scene(
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
        return (yield from self.wait_scene(
            target_ids,
            wait=self.default_wait_condition_timeout if timeout is None else float(timeout),
            label=wait_label,
        ))

    def ocr_row_clicks_in_shape(
        self,
        view: View | int | str,
        shape_title: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...] = (),
        click_target: str = "shape_center",
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
            click_target=click_target,
            occlusion_boxes=(
                self.runner._occlusion_marker_boxes(self.ctx, source_view.raw)
                if click_target == "unoccluded_text"
                else ()
            ),
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

        def visible_text_keys(frame: str) -> set[str]:
            return {
                text for row in self.ocr_fragments_in_shapes(
                    target_view, shape_titles, padding=padding,
                    frame_data_url=frame, crop=True,
                )
                if (text := _sanitize_ocr_text(row.get("text")))
            }

        for cycle_index in range(cycle_limit):
            for direction in directions:
                unchanged_before_frame: str | None = None
                unchanged_observations = 0
                for scroll_index in range(scroll_limit + 1):
                    frame = self.cur_frame(update=True)
                    # Even a visually "unchanged" scroll may reveal new rows.
                    # Always search its resulting frame before deciding to turn.
                    match = find_in_frame(frame)
                    if match is not None:
                        return match
                    if time.monotonic() >= deadline:
                        return None
                    if unchanged_before_frame is not None:
                        before_keys = visible_text_keys(unchanged_before_frame)
                        after_keys = visible_text_keys(frame)
                        if before_keys and after_keys and not (after_keys - before_keys):
                            unchanged_observations += 1
                        else:
                            # Empty OCR is missing evidence, never an endpoint.
                            unchanged_observations = 0
                        if unchanged_observations >= 2:
                            break
                    if loadable_shape is None or scroll_index >= scroll_limit:
                        break
                    changed = yield from self.scroll_shape_content(
                        loadable_shape,
                        direction=direction,
                    )
                    unchanged_before_frame = None if changed else frame
                    if changed:
                        unchanged_observations = 0
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

    def ocr_value_in_shapes(
        self,
        view: View | int | str,
        shape_titles: Iterable[str],
        *,
        parse_value: Callable[[str], Any | None],
        padding: int = 16,
        frame_data_url: str | None = None,
        crop: bool = False,
        max_attempts: int = 5,
        retry_interval: float = 2.0,
    ) -> tuple[Any | None, str]:
        """按业务格式读取数值，无法解析时只重取新帧，不重复业务动作。

        parser 返回 None 才重试，0/小数/元组等有效结果立即返回。默认
        五次、间隔两秒，可中断；耗尽保留 None 和末次原文，由业务决定失败。
        指定 frame_data_url 是同帧证据解析，仅一次；已有轮询可设 max_attempts=1。
        """
        titles = tuple(shape_titles)
        attempt = 0
        stop = self.stop_event or threading.Event()

        def read_text() -> str:
            nonlocal attempt
            self.runner._raise_if_stopped(stop)
            if attempt and frame_data_url is None:
                self.clear_frame()
            attempt += 1
            return self.ocr_text_in_shapes(
                view, titles, padding=padding,
                frame_data_url=frame_data_url, crop=crop,
            ).translate(FULLWIDTH_DIGIT_TRANSLATION)

        def wait(seconds: float) -> None:
            stop.wait(seconds)
            self.runner._raise_if_stopped(stop)

        return retry_numeric_ocr(
            read_text, parse_value, wait=wait,
            max_attempts=1 if frame_data_url is not None else max_attempts,
            retry_interval=retry_interval,
            on_retry=lambda index, text: self.runner._log(
                "warning",
                f"数值 OCR {titles} 第 {index}/{max_attempts} 次未读到有效数值，"
                f"{retry_interval:g} 秒后换帧重试；OCR={text[:120]}",
            ),
        )

    def ocr_numbers_in_shapes(
        self,
        view: View | int | str,
        shape_titles: Iterable[str],
        *,
        padding: int = 16,
        frame_data_url: str | None = None,
        crop: bool = False,
        max_attempts: int = 5,
        retry_interval: float = 2.0,
        expected_count: int | None = None,
        allow_extra_numbers: bool = False,
    ) -> tuple[list[int], str]:
        """读取整数组；实时 OCR 无有效数字时共用有界换帧重试。

        expected_count 可约束单值或分子/分母；空结果保持原有 ([], text)
        契约，已识别的 0 不会被当成空。固定帧和外层轮询不叠加重试。
        """
        values, text = self.ocr_value_in_shapes(
            view, shape_titles, padding=padding, frame_data_url=frame_data_url,
            crop=crop, max_attempts=max_attempts, retry_interval=retry_interval,
            parse_value=lambda raw: parse_ocr_values(
                raw, expected_count=expected_count,
                allow_extra_numbers=allow_extra_numbers,
            ),
        )
        return list(values or ()), text

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
            self._emit_execution_action(
                f"调整 #{target_view.id or '?'}「{label}」：{current}% -> {target}%",
                phase="execution_set_slider",
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

    def enter_daily_list_direct(
        self,
        *,
        world_view: View | int | str = 34,
        daily_view: View | int | str = 69,
        auto_route_view: View | int | str = 661,
        forbidden_view: View | int | str = 376,
        max_attempts: int = 2,
        settle_seconds: float = 0.8,
        label: str = "日常入口",
    ):
        """Open #69 only through the explicit #34 ``日常`` button.

        The generic scene planner may recover a missed world click through
        #661 ``进入``.  That control starts the game's own task auto-route: #69
        can appear briefly before the route continues into another activity.
        This exact business entrance never clicks #661: it only allows that
        overlay to disappear passively, rejects #376, and requires #69 to
        remain stable after the direct world click.
        """

        world_id = int(self.view(world_view).id)
        daily_id = int(self.view(daily_view).id)
        auto_route_id = int(self.view(auto_route_view).id)
        forbidden_id = int(self.view(forbidden_view).id)
        _wait_scene_match = yield from self.wait_scene([world_id, daily_id, auto_route_id, forbidden_id], wait=5.0, required=False)
        (scene_id, score, _frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
        )
        if scene_id == daily_id:
            return {"terminal_scene": daily_id, "attempts": 0}
        if scene_id == auto_route_id:
            # #661 is an in-world landmark/role overlay, not a world HUD
            # variant.  Its only trustworthy action is its visible ``进入``
            # control, which starts the game's unrelated auto-route.  Daily
            # entry therefore waits for the overlay to disappear without
            # clicking it and resumes only from the stable world anchor.
            landed = yield from self.wait_scene(
                [world_id, daily_id, forbidden_id],
                wait=10.0,
                label=f"{label}：等待 #661 自动消失",
            )
            scene_id = int(landed)
            if scene_id == daily_id:
                return {"terminal_scene": daily_id, "attempts": 0}
            if scene_id == forbidden_id:
                raise RuntimeError(f"{label}：#661 未经点击仍进入 #{forbidden_id}，已停止")
        if scene_id != world_id:
            raise RuntimeError(
                f"{label}：专用日常入口只能从 #{world_id} 开始，"
                f"实际 #{scene_id} ({float(score):.0f}%)"
            )

        for attempt in range(1, max(1, int(max_attempts)) + 1):
            frame = self.cur_frame(update=True)
            self.click_shape(world_id, "日常", frame_data_url=frame)
            yield from self.wait_action_settle(settle_seconds)
            try:
                landed = yield from self.wait_scene(
                    [daily_id, auto_route_id, forbidden_id],
                    wait=5.0,
                    label=f"{label}：直接打开日常列表 {attempt}",
                )
            except TimeoutError:
                landed = None
            landed_id = int(landed) if landed is not None else None
            if landed_id == forbidden_id:
                raise RuntimeError(
                    f"{label}：直接入口误入 #{landed_id}，拒绝沿任务自动寻路继续"
                )
            if landed_id == auto_route_id:
                # #661 may be the passive transition overlay produced by the
                # direct world shortcut.  Waiting is safe; clicking its
                # ``进入`` button is what starts the unrelated task route.
                landed = yield from self.wait_scene(
                    [world_id, daily_id, forbidden_id],
                    wait=10.0,
                    label=f"{label}：等待 #661 自动消失",
                )
                landed_id = int(landed)
                if landed_id == forbidden_id:
                    raise RuntimeError(
                        f"{label}：#661 未经点击仍进入 #{forbidden_id}，已停止"
                    )
                if landed_id == world_id:
                    continue
            if landed_id != daily_id:
                continue

            # #69 can be a transient waypoint of the game's own auto-route.
            # A second observation after settling proves that the list itself
            # is the terminal state before any row is searched or clicked.
            yield from self.wait_action_settle(max(1.0, settle_seconds))
            _wait_scene_match = yield from self.wait_scene([daily_id, auto_route_id, forbidden_id], wait=5.0, required=False)
            (stable_id, stable_score, _stable_frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, self.frame_data_url or "")
            )
            if stable_id == daily_id:
                return {"terminal_scene": daily_id, "attempts": attempt}
            raise RuntimeError(
                f"{label}：#{daily_id} 只是自动寻路瞬时页面，"
                f"随后进入 #{stable_id} ({float(stable_score):.0f}%)，已停止"
            )
        raise RuntimeError(
            f"{label}：从 #{world_id} 直接点击「日常」后未稳定到达 #{daily_id}"
        )

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
        if not self.runner._shape_has_click_condition(target_shape.raw):
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
        start_ratio: float | None = None,
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
            ratio_x = None if start_ratio is None else max(0.0, min(1.0, float(start_ratio)))
            start_x, start_y = live_center or (
                left + (inset_x if ratio_x is None else width * ratio_x),
                top + height / 2,
            )
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
        yield from self.runner._wait_action_settle(
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
        page_controls: Sequence[tuple[float, float]] | None = None,
        max_pages: int = 30,
        repeat_threshold: float = 96.0,
    ):
        """Find a snap page using the annotated cursor prior and safe evidence.

        ``page_controls`` supplies a complete, visually aligned set of page
        buttons in scene coordinates. These controls take precedence over
        dragging and enumerate pages independently of the initial cursor.
        ``start`` only scans in the canonical loading direction. For a bounded
        control, ``unknown`` first rewinds to the real starting edge and then
        scans forward. For a cyclic control there is no distinguished edge, so
        it scans from the current page until repetition. Static metadata guides
        intent, while unchanged/repeated frames remain the fail-closed context
        evidence. Business callers cannot opt back into an unproved reverse
        pass; cursor ambiguity must be represented by metadata or established
        by the future shared GUI-alignment layer.
        """

        target_shape = (
            view_or_shape
            if isinstance(view_or_shape, Shape) and shape is None
            else self.shape(view_or_shape, shape or "")
        )

        # Some snap carousels expose page indicators but do not accept drags.
        # Explicit controls enumerate all known pages independently of the
        # initial cursor. Discovery/alignment stays with the UI adapter.
        if page_controls:
            view = target_shape.parent_view
            if not isinstance(view, View):
                raise RuntimeError("分页控件缺少所属场景")
            for x, y in page_controls:
                self.click_frame_point(view, float(x), float(y))
                yield from self.wait_action_settle(0.8)
                page = self.paged_content_snapshot(target_shape)
                if predicate(page):
                    return page
            return None

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
        reset: bool = False,
    ) -> bool:
        """用业务可见项键统一判断滚动是否仍出现新内容。

        返回 ``False`` 表示连续多次没有任何新键，已可确认到底。动态横幅、列表
        高度动画不会进入业务键，因此比整块截图哈希可靠。
        重新打开列表开始一次独立遍历时传 reset=True，避免消费旧遍历的 seen。
        """
        target_shape = view_or_shape if isinstance(view_or_shape, Shape) else self.shape(view_or_shape, "")
        normalized_keys = {str(item).strip() for item in (visible_keys or ()) if str(item).strip()}
        shape_identity = str(target_shape.raw.get("id") or target_shape.raw.get("title") or "shape")
        state_key = f"{shape_identity}:{direction or target_shape.load_direction or 'down'}"
        states = self.attrs.setdefault("_scroll_semantic_progress", {})
        if reset:
            states.pop(state_key, None)
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

    def _ensure_daily_list_frame(self, frame: str, lines: list[dict[str, Any]], *, label: str):
        scene_id, score = self.runner._identify_scene_number(self.ctx, frame, [69, 34])
        if scene_id == 69:
            return frame, lines
        # A popup may arrive between the scroll and its observation. Use the
        # shared guarded wait before declaring the list lost; never reuse the
        # popup's OCR as list evidence after it has been dismissed.
        match = yield from self.wait_scene(
            [69], wait=20.0, label=f"{label}：处理干扰并重新确认日常列表"
        )
        if match.scene_id != 69:
            self.require_scene_repair(
                match.scene_id, match.frame_data_url,
                reason=f"{label}：日常列表已被其它场景替换，停止滚动和入口点击",
                expected_scene_ids=[69],
            )
        frame = match.frame_data_url
        lines = self.runner._ocr_fragments_in_scene_shapes(self.ctx, frame, self.view(69).raw)
        return frame, lines

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
        observations: list[dict[str, Any]] | None = None,
    ):
        """Find one #69 entry after normalizing its persisted list cursor.

        #69 preserves its scroll position after leaving the page.  Its
        ``滚动窗口`` is therefore annotated with ``loadInitialPosition=unknown``:
        first move opposite to ``loadDirection`` until two unchanged semantic
        observations prove the real starting edge, then scan forward.  Every
        frame is re-identified as #69 before another drag or click, so a stale
        cursor cannot turn into a click on another activity (notably 道法争锋).

        ``observations`` is an optional read-only per-page diagnostic sink for
        Cell reproduction: every scanned page appends ``scroll_index``,
        ``check_index``, the page's post-``_ensure_daily_list_frame``
        ``frame_data_url``, a snapshot copy of ``lines`` and the ``matches``
        returned by ``_daily_entry_matches``.  It defaults to ``None`` and
        otherwise adds no behaviour and never changes scroll/click decisions.
        """
        view69 = self.view(69)
        list_shape = self.shape(view69, "滚动窗口")
        safe_scroll_shape = self._daily_scroll_safe_shape(view69, list_shape)
        initial_checks = max(1, int(initial_checks or 1))
        initial_position = str(
            list_shape.raw.get("loadInitialPosition") or "start"
        ).strip().lower()
        if initial_position == "unknown":
            unchanged_rewinds = 0
            for rewind_index in range(max(1, int(max_scrolls))):
                before_frame = self.cur_frame(update=True)
                before_lines = self.runner._ocr_fragments_in_scene_shapes(
                    self.ctx, before_frame, view69.raw
                )
                before_frame, before_lines = yield from self._ensure_daily_list_frame(before_frame, before_lines, label=label)
                before_signature = self._daily_visible_list_signature(
                    before_lines,
                    view69,
                    region_shape=safe_scroll_shape,
                )
                changed = yield from self.scroll_shape_content(
                    view69,
                    list_shape,
                    recognition_shape=safe_scroll_shape,
                    direction="up",
                )
                after_frame = self.cur_frame(update=True)
                after_lines = self.runner._ocr_fragments_in_scene_shapes(
                    self.ctx, after_frame, view69.raw
                )
                after_frame, after_lines = yield from self._ensure_daily_list_frame(after_frame, after_lines, label=label)
                after_signature = self._daily_visible_list_signature(
                    after_lines,
                    view69,
                    region_shape=safe_scroll_shape,
                )
                if before_signature and after_signature:
                    changed = self._daily_visible_list_moved(
                        before_signature,
                        after_signature,
                    )
                if changed:
                    unchanged_rewinds = 0
                    self.runner._log(
                        "action",
                        f"{label}：归一日常列表到起点 {rewind_index + 1}",
                    )
                    continue
                unchanged_rewinds += 1
                if unchanged_rewinds >= 2:
                    break
            else:
                raise RuntimeError(
                    f"{label}：日常列表在 {int(max_scrolls)} 次反向滚动内未确认起点"
                )
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
                    self._emit_execution_action(
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
                    frame, lines = yield from self._ensure_daily_list_frame(frame, lines, label=label)
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
                    if observations is not None:
                        observations.append({
                            "scroll_index": scroll_index,
                            "check_index": check_index,
                            "frame_data_url": frame,
                            "lines": [dict(line) for line in lines],
                            "matches": list(matches),
                        })
                    if matches:
                        x, y, matched_text = matches[0]
                        progress = self._daily_entry_row_progress(lines, y)
                        if progress_can_mark_done and progress is not None and progress[0] >= progress[1]:
                            return "done"
                        if zero_progress_can_mark_done and progress is not None and progress[0] == 0:
                            return "done"
                        self._emit_execution_action(
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

    def open_navigation_list_entry(self, source_view: View, route_shape: Shape):
        """Execute a data-declared list edge for the scene navigator.

        The route Shape supplies the list viewport and OCR title pattern.  This
        operator knows only how to rewind and scan a scrollable Shape; neither
        the source nor the destination scene is encoded in the algorithm.
        Every page is recognized again before an input is sent, so a delayed
        popup cannot turn a list coordinate into an unrelated click.
        """
        data = route_shape.raw
        list_title = str(data.get("navigationListShape") or "").strip()
        title_pattern = str(data.get("navigationTitlePattern") or "").strip()
        if not list_title or not title_pattern:
            raise RuntimeError("滚动列表导航缺少 navigationListShape/navigationTitlePattern")
        list_shape = self.shape(source_view, list_title)
        scene_id = self.runner._image_number(source_view.raw)
        if scene_id is None:
            raise RuntimeError("滚动列表导航缺少来源场景 ID")
        max_scrolls = max(1, min(60, int(data.get("navigationMaxScrolls") or 30)))
        label = f"场景移动：#{scene_id}「{data.get('title') or '?'}」"
        box = self.runner._box(list_shape.raw, source_view.raw)
        left = float(box.get("x") or 0)
        top = float(box.get("y") or 0)
        right = left + float(box.get("w") or 0)
        bottom = top + float(box.get("h") or 0)

        def visible_match(lines: list[dict[str, Any]]) -> tuple[float, float, str] | None:
            for line in lines:
                value = _sanitize_ocr_text(line.get("text"))
                if not value or re.search(title_pattern, value) is None:
                    continue
                x = float(line.get("x") or 0) + float(line.get("w") or 0) / 2
                y = float(line.get("y") or 0) + float(line.get("h") or 0) / 2
                if left <= x <= right and top <= y <= bottom:
                    return x, y, value
            return None

        def confirmed_page():
            match = yield from self.wait_scene([scene_id], wait=10.0, label=label)
            if match.scene_id != scene_id:
                raise RuntimeError(
                    f"{label}：滚动列表已离开来源场景，实际 #{match.scene_id}；停止点击"
                )
            frame = match.frame_data_url
            lines = self.runner._ocr_fragments_in_scene_shapes(self.ctx, frame, source_view.raw)
            return frame, lines

        def click_visible(lines: list[dict[str, Any]], page_index: int) -> bool:
            found = visible_match(lines)
            if found is None:
                return False
            x, y, value = found
            self.runner._log("action", f"{label}：第 {page_index} 屏点击 {value}")
            self.click_frame_point(source_view, x, y)
            return True

        # A row already visible at the current cursor needs no repositioning.
        # Rewind only after confirming that this page does not contain it; the
        # viewport match above still guards against unrelated OCR text.
        initial_page = yield from confirmed_page()
        if click_visible(initial_page[1], 1):
            yield from self.wait_action_settle()
            return "open"

        # A persisted list may reopen at its previous cursor.  The list Shape
        # declares that behavior; known-start lists avoid needless gestures.
        initial_position = str(list_shape.raw.get("loadInitialPosition") or "unknown").strip().lower()
        pending_page = initial_page if initial_position == "start" else None
        if initial_position != "start":
            unchanged = 0
            for _ in range(max_scrolls):
                yield from confirmed_page()
                changed = yield from self.scroll_shape_content(
                    source_view, list_shape, direction="up", unchanged_confirmations=2,
                )
                unchanged = 0 if changed else unchanged + 1
                if unchanged >= 2:
                    break
            else:
                raise RuntimeError(f"{label}：{max_scrolls} 次反向滚动未确认列表起点")

        unchanged = 0
        for index in range(max_scrolls + 1):
            _frame, lines = pending_page if pending_page is not None else (yield from confirmed_page())
            pending_page = None
            if click_visible(lines, index + 1):
                yield from self.wait_action_settle()
                return "open"
            if index == max_scrolls:
                break
            changed = yield from self.scroll_shape_content(
                source_view, list_shape, direction="down", unchanged_confirmations=2,
            )
            unchanged = 0 if changed else unchanged + 1
            if unchanged >= 2:
                break
        return "not_found"

    def open_navigation_world_menu_entry(self, source_view: View, route_shape: Shape):
        """Click a dynamic world-menu item named by its Shape's Runtime ID.

        The menu layout and enabled functions come from the live Runtime
        inventory; OCR aligns that inventory to the current #35-like frame.
        The route Shape declares its source viewport, function ID and expected
        landing, while the navigator remains independent of business names.
        """
        from backend.core.fanxiu.instrumentation.world_menu import read_world_menu_snapshot
        from backend.core.fanxiu.runtime_gui.world_menu import plan_world_menu_click

        data = route_shape.raw
        source_id = self.runner._image_number(source_view.raw)
        if source_id is None:
            raise RuntimeError("动态菜单导航缺少来源场景 ID")
        menu_shape = str(data.get("navigationMenuShape") or "").strip()
        function_id = data.get("navigationRuntimeFunctionId")
        if not menu_shape or function_id is None:
            raise ValueError("动态菜单导航缺少 navigationMenuShape/navigationRuntimeFunctionId")
        expected = self.runner._scene_jump_target_ids(
            self.ctx.get("asset_tree") or [], data,
        )
        if not expected:
            raise ValueError("动态菜单导航缺少 sceneJumpTarget")
        match = yield from self.wait_scene([source_id], wait=10,
                                           label="场景移动：动态菜单点击前复核")
        if match.scene_id != source_id:
            raise RuntimeError(f"动态菜单导航来源已变为 #{match.scene_id}")
        snapshot = read_world_menu_snapshot()
        tokens = self.ocr_tokens_in_shapes(
            source_id, (menu_shape,), frame_data_url=match.frame_data_url,
        )
        plan = plan_world_menu_click(
            snapshot, int(function_id), tokens, expected_scene_ids=expected,
        )
        if not plan.ready or plan.point is None:
            raise RuntimeError(f"动态菜单导航无法安全定位：{plan.reason}")
        self.click_frame_point(source_view, *plan.point)
        return "open"

    def open_navigation_role_menu_entry(self, source_view: View, route_shape: Shape):
        """Open a data-declared role feature at its current Runtime slot.

        The Shape names the feature, menu area and two slot anchors. Runtime
        supplies the visible compacted slot and independently expected scene;
        the navigator checks that scene against the Shape's jump fact before
        sending an input. Its normal landing observer verifies the result.
        """
        from backend.core.fanxiu.runtime_gui.role_menu import plan_role_feature_click

        data = route_shape.raw
        source_id = self.runner._image_number(source_view.raw)
        if source_id is None:
            raise RuntimeError("角色菜单导航缺少来源场景 ID")
        name = str(data.get("navigationRuntimeName") or "").strip()
        menu_title = str(data.get("navigationMenuShape") or "").strip()
        first_title = str(data.get("navigationFirstSlotShape") or "").strip()
        second_title = str(data.get("navigationSecondSlotShape") or "").strip()
        if not all((name, menu_title, first_title, second_title)):
            raise ValueError("角色菜单导航缺少功能名、菜单区域或槽位锚点")
        expected = self.runner._scene_jump_target_ids(
            self.ctx.get("asset_tree") or [], data,
        )
        if not expected:
            raise ValueError("角色菜单导航缺少 sceneJumpTarget")
        match = yield from self.wait_scene(
            [source_id], wait=10, label="场景移动：角色菜单点击前复核",
        )
        if match.scene_id != source_id:
            raise RuntimeError(f"角色菜单导航来源已变为 #{match.scene_id}")
        plan = plan_role_feature_click(
            self, name, source_view=source_view, menu_shape=menu_title,
            first_slot_shape=first_title, second_slot_shape=second_title,
            expected_scene_ids=expected,
        )
        self.click_frame_point(source_view, *plan["point"])
        return "open"

    def open_navigation_floating_task_field(self, source_view: View, route_shape: Shape):
        """Find a named repeated task in its live scroll viewport and click a field.

        The Shape supplies the task identity, template, viewport and field;
        neither a scene route nor a screen position is built into this operator.
        A fresh source-scene observation guards each scroll and click. The
        navigator observes the actual landing and replans afterward.
        """
        data = route_shape.raw
        source_id = self.runner._image_number(source_view.raw)
        if source_id is None:
            raise RuntimeError("浮动任务导航缺少来源场景 ID")
        title = str(data.get("navigationTaskTitle") or "").strip()
        template = str(data.get("navigationTemplateShape") or "").strip()
        anchor = str(data.get("navigationAnchorField") or "").strip()
        viewport = str(data.get("navigationListShape") or "").strip()
        field = str(data.get("navigationClickField") or "").strip()
        required_text = str(data.get("navigationRequiredFieldText") or "").strip()
        match_mode = str(data.get("navigationTitleMatchMode") or "exact").strip()
        if not all((title, template, anchor, viewport, field)):
            raise ValueError("浮动任务导航缺少标题、模板、锚点、滚动窗口或点击字段")
        if match_mode not in ("exact", "contains", "name"):
            raise ValueError(f"浮动任务标题匹配方式无效：{match_mode!r}")
        if not self.runner._scene_jump_target_ids(self.ctx.get("asset_tree") or [], data):
            raise ValueError("浮动任务导航缺少 sceneJumpTarget")
        max_scrolls = max(0, min(60, int(data.get("navigationMaxScrolls") or 30)))
        for scroll_index in range(max_scrolls + 1):
            match = yield from self.wait_scene(
                [source_id], wait=10, label="场景移动：浮动任务点击前复核",
            )
            if match.scene_id != source_id:
                raise RuntimeError(f"浮动任务导航来源已变为 #{match.scene_id}")
            items = self.find_floating_items_by_anchor_text(
                source_view, template, anchor, title,
                container_shape=viewport, frame_data_url=match.frame_data_url,
                match_mode=match_mode,
            )
            if len(items) > 1:
                raise RuntimeError(f"浮动任务「{title}」在当前屏不唯一")
            if items:
                item = items[0]
                if not self.floating_item_is_fully_inside(item, viewport):
                    raise RuntimeError(f"浮动任务「{title}」位于滚动窗口边缘")
                if not self.floating_item_field_is_fully_inside(item, field, viewport):
                    raise RuntimeError(f"浮动任务「{title}」点击字段越过滚动窗口")
                if required_text:
                    observed = self.read_floating_item_field(
                        item, field, frame_data_url=match.frame_data_url,
                    )
                    if required_text not in observed:
                        raise RuntimeError(
                            f"浮动任务「{title}」字段文字不是 {required_text}：{observed!r}"
                        )
                self.click_floating_item_field(item, field)
                return "open"
            if scroll_index == max_scrolls:
                break
            if not (yield from self.scroll_shape_content(source_view, viewport)):
                break
        return "not_found"

    def open_navigation_activity_menu_item(self, source_view: View, route_shape: Shape):
        """Open a Shape-declared Runtime activity item at fresh OCR geometry.

        The existing menu helper owns menu identity, ordering, fingerprint and
        click verification. The Shape declares only the current source menu,
        its formal OCR areas, Runtime item key and expected successor scene.
        """
        from dataclasses import replace
        from backend.core.fanxiu.data_annotation.tasks.activity_menu_navigation import (
            open_loaded_activity_menu_item,
        )
        from backend.core.fanxiu.runtime_gui.activity_menu import (
            GROUP_POPUP_ACTIVITY_GRID, WORLD_LEFT_ACTIVITY_GRID,
        )

        data = route_shape.raw
        source_id = self.runner._image_number(source_view.raw)
        if source_id is None:
            raise RuntimeError("活动菜单导航缺少来源场景 ID")
        kind = str(data.get("navigationMenuKind") or "").strip()
        if kind not in ("world_left", "group_popup"):
            raise ValueError(f"活动菜单导航类型无效：{kind!r}")
        target = str(data.get("navigationRuntimeKey") or "").strip()
        shapes = data.get("navigationOcrShapes")
        fallback_shapes = data.get("navigationFallbackOcrShapes") or ()
        if not target or not isinstance(shapes, (list, tuple)) or not shapes:
            raise ValueError("活动菜单导航缺少 Runtime 键或正式 OCR Shape")
        if not isinstance(fallback_shapes, (list, tuple)):
            raise ValueError("活动菜单导航的备用 OCR Shape 无效")
        expected = self.runner._scene_jump_target_ids(
            self.ctx.get("asset_tree") or [], data,
        )
        if len(expected) != 1:
            raise ValueError("活动菜单导航必须声明唯一后继场景")
        match = yield from self.wait_scene(
            [source_id], wait=10, label="场景移动：活动菜单点击前复核",
        )
        if match.scene_id != source_id:
            raise RuntimeError(f"活动菜单导航来源已变为 #{match.scene_id}")
        grid = (
            WORLD_LEFT_ACTIVITY_GRID if kind == "world_left"
            else GROUP_POPUP_ACTIVITY_GRID
        )
        offset = data.get("navigationClickOffsetHeights")
        if offset is not None:
            grid = replace(grid, click_offset_heights=float(offset))
        yield from open_loaded_activity_menu_item(
            self, target, kind=kind, source_scene_id=source_id,
            ocr_shape_names=tuple(str(value) for value in shapes),
            expected_scene_ids=expected,
            target_gui_name=str(data.get("navigationGuiName") or "").strip() or None,
            fallback_ocr_shape_names=tuple(str(value) for value in fallback_shapes),
            grid=grid,
            max_scrolls=max(0, min(60, int(data.get("navigationMaxScrolls") or 0))),
        )
        return "open"

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
