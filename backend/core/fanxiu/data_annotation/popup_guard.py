from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pyxllib.autogui import CloseActionPlanner, Shape, View


class PopupGuardMixin:
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

    def _best_popup_47_child(self, runtime: Any, popup_view: View) -> tuple[View | None, float]:
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
        runtime: Any,
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
        runtime: Any,
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
        runtime: Any,
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

    def _close_popup_view_without_confirm(self, runtime: Any, view: View, event: dict[str, Any]) -> bool:
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
        scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        for candidate, score in zip(candidates, scores):
            if score >= self.overlay_threshold:
                return candidate, score
        return None, 0.0

    def _auto_close_popup_guard_step(
        self,
        runtime: Any,
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
