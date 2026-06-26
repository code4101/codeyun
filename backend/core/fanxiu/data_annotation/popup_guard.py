from __future__ import annotations

import base64
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pyxllib.autogui import CloseActionPlanner, Shape, View

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


class PopupGuardMixin:
    def _disconnect_popup_frame_size(self, frame_data_url: str) -> tuple[int, int]:
        try:
            _header, encoded = frame_data_url.split(",", 1) if "," in frame_data_url else ("", frame_data_url)
            raw = base64.b64decode(encoded)
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as image:
                return int(image.size[0]), int(image.size[1])
        except Exception:
            return 900, 1600

    def _disconnect_popup_detected(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return (
            "断线重连" in compact
            or "网络已断开" in compact
            or ("请重新登录" in compact and "重连" in compact)
        )

    def _disconnect_popup_button_center(
        self,
        lines: list[dict[str, Any]],
        *,
        width: int,
        height: int,
    ) -> tuple[float, float, str, str]:
        preferred = ("重连", "重新登录")
        min_button_y = height * 0.45
        candidates: list[tuple[int, float, float, str, str]] = []
        for line in lines:
            text = _sanitize_ocr_text(line.get("text"))
            if not text:
                continue
            x = float(line.get("x") or 0)
            y = float(line.get("y") or 0)
            w = float(line.get("w") or 0)
            h = float(line.get("h") or 0)
            if w <= 0 or h <= 0:
                continue
            center_y = y + h / 2
            if center_y < min_button_y:
                continue
            for index, label in enumerate(preferred):
                if label in text:
                    candidates.append((index, x + w / 2, center_y, label, text))
                    break
        if candidates:
            _index, x, y, label, text = sorted(candidates, key=lambda item: (item[0], item[2], item[1]))[0]
            return x, y, label, text
        # The disconnect dialog has a stable two-button layout. If OCR proves this
        # exact system dialog but misses the button glyphs, prefer the right-side
        # reconnect button over restarting the emulator or clicking random areas.
        return width * 0.66, height * 0.66, "重连", "layout_fallback"

    def _handle_disconnect_reconnect_popup(self, runtime: Any) -> bool | str:
        try:
            frame = runtime.cur_frame(update=False)
            lines = runtime.ocr_lines(frame)
            text = runtime.ocr_text(frame)
        except Exception:
            return False
        if not self._disconnect_popup_detected(text):
            with self._lock:
                if self._status.get("network_disconnect_reconnect_attempts"):
                    self._status["network_disconnect_reconnect_attempts"] = 0
            return False

        with self._lock:
            attempts = int(self._status.get("network_disconnect_reconnect_attempts") or 0)
            if attempts >= 3:
                event = {
                    "time": time.time(),
                    "kind": "network_disconnect",
                    "image": "OCR",
                    "title": "断线重连",
                    "folder_path": "runtime/ocr",
                    "score": 100.0,
                    "action": "device_recovery_required",
                    "ocr": _sanitize_ocr_text(text)[:80],
                    "attempts": attempts,
                }
                self._status.update({
                    "current_scene": None,
                    "message": f"断线重连点击「重连」连续 {attempts} 次无效，停止弹窗守护并要求重启 MuMu/设备恢复",
                    "last_guard_event": event,
                    "network_disconnect_reconnect_blocked": True,
                    "updated_at": time.time(),
                })
                self._log_locked("error", self._status["message"])
                return "blocked"
            self._status["network_disconnect_reconnect_attempts"] = attempts + 1

        width, height = self._disconnect_popup_frame_size(frame)
        x, y, action, source = self._disconnect_popup_button_center(lines, width=width, height=height)
        click_image = {
            "type": "image",
            "title": "断线重连OCR",
            "filename": "runtime_disconnect_reconnect.png",
            "width": width,
            "height": height,
            "shapes": [],
        }
        runtime.click_frame_point(click_image, x, y)
        event = {
            "time": time.time(),
            "kind": "network_disconnect",
            "image": "OCR",
            "title": "断线重连",
            "folder_path": "runtime/ocr",
            "score": 100.0,
            "action": f"click:{action}",
            "ocr": _sanitize_ocr_text(text)[:80],
            "button_source": source,
            "attempts": attempts + 1,
        }
        self._record_popup_guard_click(
            None,
            f"守护处理：断线重连 OCR 点击「{action}」",
            event,
            action,
        )
        return True

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

    def _auto_close_guard_action_allowed(self, shape: dict[str, Any] | None) -> bool:
        if not isinstance(shape, dict):
            return False
        text = f"{shape.get('title') or ''}\n{shape.get('description') or ''}"
        blocked_markers = (
            "不作为通用弹窗守护动作",
            "只允许",
            "不得点击",
            "不能点击",
            "禁止点击",
            "托管中不得点击",
            "不得自动点击",
        )
        return not any(marker in text for marker in blocked_markers)

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
        if child_view is None or child_score < self._runtime_popup_guard_min_score(runtime):
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
        action_shape = self._auto_close_guard_action_shape(child_view.raw)
        if not self._auto_close_guard_action_allowed(action_shape):
            return True
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
        if child_view is None or child_score < self._runtime_popup_guard_min_score(runtime):
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
        if child_view is None or child_score < self._runtime_popup_guard_min_score(runtime):
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
        workers = min(len(candidates), 32)
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
        *,
        min_score: float | None = None,
    ) -> tuple[dict[str, Any] | None, float]:
        if not candidates:
            return None, 0.0
        threshold = self.overlay_threshold if min_score is None else float(min_score)
        scores = self._auto_close_popup_candidate_scores_parallel(ctx, candidates, frame_data_url)
        for candidate, score in zip(candidates, scores):
            if score >= threshold:
                return candidate, score
        return None, 0.0

    def _runtime_popup_guard_min_score(self, runtime: Any) -> float:
        try:
            return float(runtime.attrs.get("popup_guard_min_score") or self.overlay_threshold)
        except Exception:
            return float(self.overlay_threshold)

    def _auto_close_popup_guard_step(
        self,
        runtime: Any,
        *,
        allow_confirm_actions: bool = True,
        during_task: bool | None = None,
    ) -> bool:
        if during_task is None:
            try:
                with self._lock:
                    phase = str(self._status.get("phase") or "")
                    during_task = bool(self._status.get("running")) and phase not in {"", "idle", "idle_guard"}
            except Exception:
                during_task = False
        min_score = (
            max(float(self.overlay_threshold), float(getattr(self, "task_popup_guard_threshold", self.overlay_threshold)))
            if during_task
            else float(self.overlay_threshold)
        )
        had_previous_min_score = "popup_guard_min_score" in runtime.attrs
        previous_min_score = runtime.attrs.get("popup_guard_min_score")
        runtime.attrs["popup_guard_min_score"] = min_score
        try:
            disconnect_result = self._handle_disconnect_reconnect_popup(runtime)
            if disconnect_result is True:
                return True
            if disconnect_result == "blocked":
                return False

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
                if not self._auto_close_guard_action_allowed(matched.action_shape):
                    return True
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
        finally:
            if had_previous_min_score:
                runtime.attrs["popup_guard_min_score"] = previous_min_score
            else:
                runtime.attrs.pop("popup_guard_min_score", None)
