from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import GeneratorType
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)


class SignupMiscTaskMixin:
    def _execute_daily_signup_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
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
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event, payload)
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
            list_result = self._execute_daily_signup_signup_list(ctx, stop_event, payload)
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
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event, payload)
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
            entry_result = self._execute_daily_signup_activity_entry(ctx, stop_event, payload)
            entry_status = (yield from entry_result) if isinstance(entry_result, GeneratorType) else entry_result
            if entry_status != "success":
                return entry_status or "success"
        return result

    def _execute_daily_signup_activity_entry(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
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
            self._record_daily_signup_done(payload, message="未识别到「领」，今日报名已完成")
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
        list_result = self._execute_daily_signup_signup_list(ctx, stop_event, payload)
        list_status = (yield from list_result) if isinstance(list_result, GeneratorType) else list_result
        return list_status or "success"

    def _execute_daily_signup_signup_list(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
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
        try:
            yield from self._wait_scene_id(ctx, stop_event, 69, timeout=12.0, label="日常_报名：返回日常 #69")
            returned_scene_id = 69
        except RuntimeError as exc:
            returned_scene_id = None
            frame_after_return = self._screencap(ctx)
            scene_id, score = self._identify_scene_number(ctx, frame_after_return, [69, 34, 23])
            if scene_id in {69, 34}:
                returned_scene_id = scene_id
            with self._lock:
                self._log_locked(
                    "warn",
                    f"日常_报名：报名已处理完成，但返回日常收尾识别失败：{exc}；"
                    f"当前 {'#' + str(scene_id) if scene_id else 'unknown'} {score:.0f}%，按已完成处理避免重复报名",
                )

        with self._lock:
            self._set_status_locked(
                "running",
                f"日常_报名：报名处理完成，领取 {clicked_count} 个"
                + (f"，已返回 #{returned_scene_id}" if returned_scene_id else "，收尾返回未确认"),
                phase="daily_signup_done",
                current_scene=returned_scene_id or 23,
            )
            self._log_locked(
                "success",
                f"日常_报名：报名处理完成，领取 {clicked_count} 个"
                + (f"，已返回 #{returned_scene_id}" if returned_scene_id else "，收尾返回未确认"),
            )
        self._record_daily_signup_done(payload, message=f"报名处理完成，领取 {clicked_count} 个")
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
        self._drag_frame_point(ctx, image, x, start_y, x, end_y, duration_ms=1500)

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
