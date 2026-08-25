from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pyxllib.autogui import CloseActionPlanner, Shape, View

from backend.core.fanxiu.data_annotation.recognition_graph import SceneGraphCandidate, choose_scene_from_graph
class FanxiuEmulatorRestartRequired(RuntimeError):
    """Signal that scene #433 forced a full MuMu restart.

    The interrupted business Cell must never continue its generator after this
    signal.  A managed task may finish startup navigation to #34, then Scheduler
    records the attempt as failed and retries the whole job as a fresh run.
    """

    def __init__(
        self,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
        recovery_succeeded: bool = False,
    ) -> None:
        super().__init__(message)
        self.detail = message
        self.evidence = dict(evidence or {})
        self.recovery_succeeded = bool(recovery_succeeded)


class SceneInterruptionMixin:
    """Popup candidate indexing and actions used by unified scene recognition."""
    _LEAVE_CONFIRM_VIEW_IDS = (289, 86)

    def _handle_disconnect_reconnect_popup(self, _runtime: Any) -> bool:
        """Retired compatibility hook; disconnects require a formal popup scene."""

        return False

    def _skip_popup_guard_on_login_or_maintenance(self, _runtime: Any) -> bool:
        """Retired compatibility hook; startup pages are recognized by the graph."""

        return False

    def _is_independent_exit_shape(self, shape: dict[str, Any]) -> bool:
        return CloseActionPlanner().is_independent_exit_shape(shape)

    def _auto_close_guard_action_shape(self, image: dict[str, Any]) -> dict[str, Any] | None:
        # Fanxiu annotation convention: in the top-level popup group, "空白" means
        # the background/overlay area that closes the popup when tapped. Prefer it
        # over tiny close buttons and "确定", which may trigger extra scene changes.
        planner = CloseActionPlanner(title_priorities=("空白", "关闭", "返回"))
        action_shape = planner.choose_close_shape(image.get("shapes"), include_independent_exit=True)
        if action_shape is not None:
            return action_shape
        # A popup node inside the explicit ``弹窗`` domain is itself the
        # authorization boundary for interruption handling.  Fanxiu uses both
        # ``确定`` and ``确认`` for acknowledgement-only buttons, so index
        # either spelling as the node's executable fallback action.
        return CloseActionPlanner(title_priorities=("确定", "确认")).choose_close_shape(image.get("shapes"))

    def _auto_close_guard_action_allowed(self, shape: dict[str, Any] | None) -> bool:
        if not isinstance(shape, dict):
            return False
        text = f"{shape.get('title') or ''}\n{shape.get('description') or ''}"
        blocked_markers = (
            "不作为通用弹窗处理动作",
            "只允许",
            "不得点击",
            "不能点击",
            "禁止点击",
            "托管中不得点击",
            "不得自动点击",
        )
        return not any(marker in text for marker in blocked_markers)

    def _popup_candidate_has_executable_action(
        self,
        image: dict[str, Any],
        action_shape: dict[str, Any] | None,
    ) -> bool:
        """Only inject popup nodes with an executable interruption action."""

        scene_id = self._image_number(image)
        specialized_scene_ids = {
            84,
            287,
            300,
            355,
            393,
            # #546 is an actionless login maintenance prompt.  It must join
            # the global interruption graph so ordinary business jobs can
            # promote it into the maintenance domain before #47's generic
            # close action runs.  BehaviorTreeRuntime handles #546 by raising
            # FanxiuMaintenanceDetected and never consumes its confirm button.
            546,
            *self._LEAVE_CONFIRM_VIEW_IDS,
        }
        return (
            scene_id in specialized_scene_ids
            or self._auto_close_guard_action_allowed(action_shape)
        )

    def _index_guard_candidates(self, nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Flatten executable identity-bearing global interruption scenes.

        The regular source is every ``弹窗`` folder. A business scene can also
        opt in explicitly with ``runtimeInterruption=true`` when the game may
        surface it over unrelated jobs. This keeps its business grouping and
        avoids unsafe heuristics such as treating every scene with a Return
        button as a popup. An actionless scene is never indexed.
        """

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

        def collect_popup_scenes(items: list[dict[str, Any]], path: list[str]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                node_type = str(item.get("type") or "")
                title = str(item.get("title") or "").strip()
                current_path = [*path, title] if title else path
                if node_type == "image" and self._scene_identity_shapes(item):
                    action_shape = self._auto_close_guard_action_shape(item)
                    if self._popup_candidate_has_executable_action(item, action_shape):
                        add_candidate(item, "/".join(path), action_shape)
                children = item.get("children")
                if isinstance(children, list):
                    collect_popup_scenes([child for child in children if isinstance(child, dict)], current_path)

        def find_popup_groups(items: list[dict[str, Any]], path: list[str]) -> None:
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                current_path = [*path, title] if title else path
                children = item.get("children")
                if (
                    str(item.get("type") or "") == "image"
                    and item.get("runtimeInterruption") is True
                    and self._scene_identity_shapes(item)
                ):
                    action_title = str(item.get("runtimeInterruptionAction") or "").strip()
                    action_shape = next(
                        (
                            shape
                            for shape in item.get("shapes") or []
                            if isinstance(shape, dict)
                            and str(shape.get("title") or "").strip() == action_title
                        ),
                        None,
                    ) if action_title else None
                    if self._popup_candidate_has_executable_action(item, action_shape):
                        add_candidate(item, "/".join(path), action_shape)
                if str(item.get("type") or "") == "folder" and title == "弹窗":
                    if isinstance(children, list):
                        collect_popup_scenes(
                            [child for child in children if isinstance(child, dict)],
                            current_path,
                        )
                    continue
                if isinstance(children, list):
                    find_popup_groups([child for child in children if isinstance(child, dict)], current_path)

        find_popup_groups(nodes, [])
        return candidates

    def _handle_auto_close_popup_84(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
        allow_confirm_actions: bool = True,
    ) -> bool:
        no_more_prompt = view.get_shape("不再提示")
        if no_more_prompt is not None and not no_more_prompt.is_match(runtime):
            runtime.click_shape(view, no_more_prompt, frame_data_url=runtime.cur_frame())
            self._record_popup_guard_click(84, f"场景识别处理：#84 点击「不再提示」 {score:.0f}%", event, "不再提示")
            return True

        confirm_shape = view.get_shape("确认")
        if confirm_shape is None:
            self._record_popup_guard_missing(84, f"场景识别命中：#84 {score:.0f}%，缺少「确认」标注", event, "missing_confirm")
            return True
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, view, event)
        runtime.click_shape(view, confirm_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(84, f"场景识别处理：#84 点击「确认」 {score:.0f}%", event, "确认")
        return True

    def _handle_auto_close_leave_confirm_popup(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
    ) -> bool:
        view_id = int(view.id or 0)
        view_label = f"#{view_id}" if view_id else "#?"
        confirm_shape = view.get_shape("确认")
        if not confirm_shape:
            self._record_popup_guard_missing(view_id or None, f"场景识别命中：{view_label} {score:.0f}%，缺少「确认」标注", event, "missing_confirm")
            return True
        runtime.click_shape(view, confirm_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(view_id or None, f"场景识别处理：{view_label} 点击「确认」 {score:.0f}%", event, "确认")
        return True

    def _handle_auto_close_popup_287(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
        allow_confirm_actions: bool = True,
    ) -> bool:
        confirm_shape = view.get_shape("确认")
        if not confirm_shape:
            self._record_popup_guard_missing(287, f"场景识别命中：#287 {score:.0f}%，缺少「确认」标注", event, "missing_confirm")
            return True
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, view, event)
        runtime.click_shape(view, confirm_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(287, f"场景识别处理：#287 点击「确认」 {score:.0f}%", event, "确认")
        return True

    def _handle_auto_close_popup_355(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
    ) -> bool:
        cancel_shape = view.get_shape("取消")
        if not cancel_shape:
            self._record_popup_guard_missing(355, f"场景识别命中：#355 {score:.0f}%，缺少「取消」标注", event, "missing_cancel")
            return True
        runtime.click_shape(view, cancel_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(355, f"场景识别处理：#355 点击「取消」 {score:.0f}%", event, "取消")
        return True

    def _handle_auto_close_popup_393(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
    ) -> bool:
        """进入社团大比干扰页时选择分身，释放当前业务流程。"""

        avatar_shape = view.get_shape("分身")
        if not avatar_shape:
            self._record_popup_guard_missing(
                393,
                f"场景识别命中：#393 {score:.0f}%，缺少「分身」标注",
                event,
                "missing_avatar",
            )
            return True
        runtime.click_shape(view, avatar_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(393, f"场景识别处理：#393 点击「分身」 {score:.0f}%", event, "分身")
        return True

    def _handle_auto_close_confirm_popup(
        self,
        runtime: Any,
        view: View,
        event: dict[str, Any],
        *,
        score: float,
        allow_confirm_actions: bool = True,
    ) -> bool:
        view_id = int(view.id or 0)
        view_label = f"#{view_id}" if view_id else "#?"
        confirm_shape = view.get_shape("确认")
        if not confirm_shape:
            self._record_popup_guard_missing(view_id or None, f"场景识别命中：{view_label} {score:.0f}%，缺少「确认」标注", event, "missing_confirm")
            return True
        if not allow_confirm_actions:
            return self._close_popup_view_without_confirm(runtime, view, event)
        runtime.click_shape(view, confirm_shape, frame_data_url=runtime.cur_frame())
        self._record_popup_guard_click(view_id or None, f"场景识别处理：{view_label} 点击「确认」 {score:.0f}%", event, "确认")
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

    def _close_popup_view_without_confirm(self, runtime: Any, view: View, event: dict[str, Any]) -> bool:
        action_shape = runtime.matched_view.action_shape if runtime.matched_view is not None else None
        if (
            not isinstance(action_shape, dict)
            or str(action_shape.get("title") or "").strip() in {"确定", "确认"}
        ):
            return False
        shape = Shape(action_shape, parent_view=view)
        runtime.click_shape(view, shape)
        scene_id = view.id
        image_label = f"#{scene_id}" if scene_id is not None else view.title or view.filename or "unknown"
        action_title = shape.title or "shape"
        self._record_popup_guard_click(
            scene_id,
            f"场景识别处理：{image_label} 点击「{action_title}」 {event.get('score', 0):.0f}%",
            event,
            action_title,
        )
        return True

    def _handle_recognized_popup_candidate(
        self,
        runtime: Any,
        candidate: dict[str, Any],
        *,
        score: float,
        allow_confirm_actions: bool = True,
    ) -> bool:
        """Execute the action bound to the popup node selected by the graph.

        Recognition is intentionally not repeated here.  The caller already
        resolved one combined business+popup Layer 0; this method only executes
        the winning popup node and lets the caller repeat the same recognition.
        """

        image = candidate.get("image")
        if not isinstance(image, dict):
            return False
        view = View(image)
        image_label = f"#{view.id}" if view.id is not None else view.title or view.filename or "unknown"
        event = {
            "time": time.time(),
            "kind": "popup",
            "image": image_label,
            "title": view.title,
            "folder_path": str(candidate.get("folder_path") or ""),
            "score": round(float(score or 0.0), 1),
            "action": "",
        }

        if view.id == 84:
            return self._handle_auto_close_popup_84(
                runtime,
                view,
                event,
                score=score,
                allow_confirm_actions=allow_confirm_actions,
            )
        if view.id in self._LEAVE_CONFIRM_VIEW_IDS:
            return self._handle_auto_close_leave_confirm_popup(
                runtime,
                view,
                event,
                score=score,
            )
        if view.id == 287:
            return self._handle_auto_close_popup_287(
                runtime,
                view,
                event,
                score=score,
                allow_confirm_actions=allow_confirm_actions,
            )
        if view.id == 300:
            return self._handle_auto_close_confirm_popup(
                runtime,
                view,
                event,
                score=score,
                allow_confirm_actions=allow_confirm_actions,
            )
        if view.id == 355:
            return self._handle_auto_close_popup_355(
                runtime,
                view,
                event,
                score=score,
            )
        if view.id == 393:
            return self._handle_auto_close_popup_393(
                runtime,
                view,
                event,
                score=score,
            )
        action_shape = candidate.get("action_shape")
        try:
            if not self._auto_close_guard_action_allowed(action_shape):
                return False
            if (
                not allow_confirm_actions
                and str(action_shape.get("title") or "").strip() in {"确定", "确认"}
            ):
                return False
            # Execute the exact action selected while indexing this popup
            # candidate.  Re-running View.close() here would invoke a second,
            # potentially different close-action planner and could miss a
            # perfectly valid ``确认`` annotation.
            shape = Shape(action_shape, parent_view=view)
            runtime.click_shape(view, shape, frame_data_url=runtime.cur_frame())
        except RuntimeError:
            self._record_popup_guard_missing(
                view.id,
                f"弹窗处理中断命中：{image_label} {score:.0f}%，缺少关闭标注",
                event,
                "missing_action",
            )
            return False
        action_title = shape.title or "shape"
        self._record_popup_guard_click(
            view.id,
            f"场景识别处理：{image_label} 点击「{action_title or 'shape'}」 {score:.0f}%",
            event,
            action_title or "shape",
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
        candidates = self._auto_close_guard_images(self._resolved_asset_tree(tree))
        self._auto_close_candidates_cache[cache_key] = (signature[0], signature[1], candidates)
        return candidates

    def _auto_close_popup_candidate_score(self, ctx: dict[str, Any], candidate: dict[str, Any], frame_data_url: str) -> float:
        image = candidate.get("image")
        if not isinstance(image, dict):
            return 0.0
        return self._scene_score(ctx, image, frame_data_url)

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
        workers = min(len(candidates), 32)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fanxiu-popup-match") as executor:
            return list(executor.map(lambda candidate: self._auto_close_popup_candidate_score(ctx, candidate, frame_data_url), candidates))

    def _auto_close_popup_graph_match(
        self,
        ctx: dict[str, Any],
        candidates: list[dict[str, Any]],
        frame_data_url: str,
        *,
        min_score: float | None = None,
    ) -> tuple[dict[str, Any] | None, float]:
        """Recognize one popup from a single flattened Layer-0 graph pass."""

        if not candidates:
            return None, 0.0
        guard_threshold = self.overlay_threshold if min_score is None else float(min_score)
        scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        graph_candidates: list[SceneGraphCandidate] = []
        candidate_by_scene_id: dict[int, dict[str, Any]] = {}
        for candidate, score in zip(candidates, scores):
            image = candidate.get("image")
            if not isinstance(image, dict):
                continue
            scene_id = self._image_number(image)
            if scene_id is None:
                continue
            scene_id = int(scene_id)
            candidate_by_scene_id.setdefault(scene_id, candidate)
            threshold = max(guard_threshold, float(self._scene_match_threshold(scene_id)))
            graph_candidates.append(
                SceneGraphCandidate(
                    scene_id=scene_id,
                    score=float(score or 0.0),
                    matched=float(score or 0.0) >= threshold,
                )
            )

        matched_ids = [item.scene_id for item in graph_candidates if item.matched]
        edges: list[dict[str, Any]] = []
        if len(matched_ids) > 1:
            edges.extend(self._scene_match_edges_for_candidates(ctx, matched_ids))
            graph_candidates = [
                SceneGraphCandidate(
                    scene_id=item.scene_id,
                    score=item.score,
                    matched=item.matched,
                    frame_similarity=(
                        self._scene_reference_similarity(ctx, candidate_by_scene_id[item.scene_id]["image"], frame_data_url)
                        if item.matched
                        else None
                    ),
                )
                for item in graph_candidates
            ]
        result = choose_scene_from_graph(graph_candidates, edges)
        if result.scene_id is None:
            return None, 0.0
        return candidate_by_scene_id.get(int(result.scene_id)), float(result.score or 0.0)
