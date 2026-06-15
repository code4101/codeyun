from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


class DailyYihuoTaskMixin:
    def _execute_daily_yihuo_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_异火资产树路径，无法执行作业")

        runtime = self._fanxiu_runtime(ctx, asset_tree_path, stop_event=stop_event)
        recovered_local_page = yield from self._recover_daily_yihuo_local_pages(runtime, stop_event)
        if recovered_local_page:
            yield from runtime.wait_view(34, timeout=float(payload.get("world_return_timeout") or 20.0), label="日常_异火：局部页收尾后等待回到 #34 世界")
            next_time = self._next_daily_boss_reset_time_text()
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-yihuo")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="daily_yihuo",
                label="日常_异火",
                last_result="success",
            )
            with self._lock:
                self._set_status_locked(
                    "success",
                    f"日常_异火已从局部页返回世界，下次 {next_time}",
                    phase="daily_yihuo_done",
                    current_scene=34,
                )
                self._log_locked("success", self._status["message"])
            return "success"
        with self._lock:
            self._set_status_locked("running", "日常_异火：进入世界 #34", phase="daily_yihuo_go_world")
        yield from runtime.goto_view(34)
        with self._lock:
            self._set_status_locked("running", "日常_异火：打开 #35 下方菜单", phase="daily_yihuo_open_bottom_menu", current_scene=34)
            self._log_locked("action", "日常_异火：点击 #34「打开下方菜单」")
        yield from runtime.wait_click(34, "打开下方菜单", timeout=float(payload.get("bottom_menu_click_timeout") or 18.0))
        yield from runtime.wait_view(35, timeout=float(payload.get("bottom_menu_timeout") or 8.0), label="日常_异火：等待 #35 下方菜单")
        with self._lock:
            self._set_status_locked("running", "日常_异火：点击 #35「菜单/异火」", phase="daily_yihuo_open_yihuo", current_scene=35)
            self._log_locked("action", "日常_异火：点击 #35「菜单/异火」")
        yield from runtime.wait_click(35, "菜单/异火", timeout=float(payload.get("yihuo_click_timeout") or 18.0))
        with self._lock:
            self._set_status_locked("running", "日常_异火：点击 #259「异火」", phase="daily_yihuo_click_yihuo", current_scene=259)
            self._log_locked("action", "日常_异火：点击 #259「异火」")
        yield from runtime.wait_click(259, "异火", timeout=float(payload.get("yihuo_page_timeout") or 12.0))
        with self._lock:
            self._set_status_locked("running", "日常_异火：点击 #260「净莲」", phase="daily_yihuo_click_jinglian", current_scene=260)
            self._log_locked("action", "日常_异火：点击 #260「净莲」")
        yield from runtime.wait_click(260, "净莲", timeout=float(payload.get("jinglian_page_timeout") or 12.0))
        already_claimed = yield from self._wait_daily_yihuo_box_or_claimed(runtime, stop_event, timeout=float(payload.get("box_page_timeout") or 12.0))
        if already_claimed:
            with self._lock:
                self._set_status_locked("running", "日常_异火：#261 已领取，跳过箱子点击", phase="daily_yihuo_box_claimed", current_scene=261)
                self._log_locked("success", self._status["message"])
        else:
            with self._lock:
                self._set_status_locked("running", "日常_异火：点击 #261「箱子」", phase="daily_yihuo_click_box", current_scene=261)
                self._log_locked("action", "日常_异火：点击 #261「箱子」")
            yield from runtime.wait_click(261, "箱子", timeout=float(payload.get("box_click_timeout") or 12.0))
        with self._lock:
            self._set_status_locked("running", "日常_异火：点击 #261「返回」", phase="daily_yihuo_return_from_box", current_scene=261)
            self._log_locked("action", "日常_异火：点击 #261「返回」")
        yield from runtime.wait_click(261, "返回", timeout=float(payload.get("box_return_timeout") or 12.0))
        with self._lock:
            self._set_status_locked("running", "日常_异火：点击 #260「返回」", phase="daily_yihuo_return_from_jinglian", current_scene=260)
            self._log_locked("action", "日常_异火：点击 #260「返回」")
        yield from runtime.wait_click(260, "返回", timeout=float(payload.get("jinglian_return_timeout") or 12.0))
        returned_world = yield from self._wait_daily_yihuo_world_or_shape_visible(
            runtime,
            stop_event,
            259,
            "返回",
            timeout=float(payload.get("yihuo_return_timeout") or 12.0),
        )
        if not returned_world:
            with self._lock:
                self._set_status_locked("running", "日常_异火：点击 #259「返回」", phase="daily_yihuo_return_to_world", current_scene=259)
                self._log_locked("action", "日常_异火：点击 #259「返回」")
            yield from runtime.wait_click(259, "返回", timeout=float(payload.get("world_return_timeout") or 20.0))
            yield from runtime.wait_view(34, timeout=float(payload.get("world_return_timeout") or 20.0), label="日常_异火：等待回到 #34 世界")
        next_time = self._next_daily_boss_reset_time_text()
        scheduler_task_id = str(payload.get("__scheduler_task_id") or "legacy-daily-yihuo")
        self._record_scheduler_task_discovered_next_time(
            scheduler_task_id,
            next_time,
            task_type="daily_yihuo",
            label="日常_异火",
            last_result="success",
        )
        yield from self._wait_runtime_action_settle(
            ctx,
            stop_event,
            seconds=float(payload.get("yihuo_settle_seconds") or 1.5),
        )
        with self._lock:
            self._set_status_locked(
                "success",
                f"日常_异火完成，已回到世界，下次 {next_time}",
                phase="daily_yihuo_done",
                current_scene=34,
            )
            self._log_locked("success", self._status["message"])
        return "success"

    def _daily_yihuo_view_image(self, runtime: Any, view_id: int) -> dict[str, Any]:
        view = runtime.get_view(view_id)
        if not isinstance(view, View) or not isinstance(view.raw, dict):
            raise RuntimeError(f"日常_异火：缺少 #{view_id} 标注")
        return view.raw

    def _daily_yihuo_box_already_claimed_text(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", _sanitize_ocr_text(text))
        return "已领取" in compact and ("次日5点刷新" in compact or "净莲妖火" in compact)

    def _wait_daily_yihuo_world_or_shape_visible(
        self,
        runtime: Any,
        stop_event: threading.Event,
        view_id: int,
        shape_title: str,
        *,
        timeout: float,
        threshold: float = 80.0,
    ):
        ctx = runtime.ctx
        image = self._daily_yihuo_view_image(runtime, view_id)
        shape = self._find_shape(image, shape_title)
        image_id = image.get("id") or _runtime_image_number(image) or "?"
        if shape is None:
            raise RuntimeError(f"日常_异火：缺少 #{image_id}「{shape_title}」标注")
        deadline = time.time() + max(1.0, float(timeout))
        last_score = 0.0
        last_world_score = 0.0
        while time.time() < deadline:
            if stop_event.is_set():
                raise InterruptedError()
            frame = self._screencap(ctx)
            scene_id, scene_score = self._identify_scene_number(ctx, frame, [34])
            last_world_score = float(scene_score or 0.0)
            if scene_id == 34 and last_world_score >= threshold:
                with self._lock:
                    self._set_status_locked("running", f"日常_异火：已回到 #34 世界 {last_world_score:.0f}%", phase="daily_yihuo_wait_world_or_shape", current_scene=34)
                    self._log_locked("success", self._status["message"])
                return True
            last_score = float(self._shape_score(ctx, image, shape, frame) or 0.0)
            if last_score >= threshold:
                with self._lock:
                    self._set_status_locked("running", f"日常_异火：#{image_id}「{shape_title}」已命中 {last_score:.0f}%", phase="daily_yihuo_wait_world_or_shape", current_scene=image_id if isinstance(image_id, int) else None)
                    self._log_locked("success", self._status["message"])
                return False
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=0.5)
        raise RuntimeError(
            f"日常_异火：等待 #34 或 #{image_id}「{shape_title}」超时，"
            f"最后世界分数 {last_world_score:.0f}%，最后按钮分数 {last_score:.0f}%"
        )

    def _wait_daily_yihuo_box_or_claimed(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        timeout: float,
        threshold: float = 80.0,
    ):
        ctx = runtime.ctx
        image261 = self._daily_yihuo_view_image(runtime, 261)
        box_shape = self._find_shape(image261, "箱子")
        return_shape = self._find_shape(image261, "返回")
        image_id = image261.get("id") or _runtime_image_number(image261) or "?"
        if box_shape is None:
            raise RuntimeError(f"日常_异火：缺少 #{image_id}「箱子」标注")
        if return_shape is None:
            raise RuntimeError(f"日常_异火：缺少 #{image_id}「返回」标注")
        deadline = time.time() + max(1.0, float(timeout))
        last_box_score = 0.0
        last_return_score = 0.0
        while time.time() < deadline:
            if stop_event.is_set():
                raise InterruptedError()
            frame = self._screencap(ctx)
            last_box_score = float(self._shape_score(ctx, image261, box_shape, frame) or 0.0)
            last_return_score = float(self._shape_score(ctx, image261, return_shape, frame) or 0.0)
            if last_box_score >= threshold:
                with self._lock:
                    self._set_status_locked("running", f"日常_异火：#{image_id}「箱子」已命中 {last_box_score:.0f}%", phase="daily_yihuo_wait_shape", current_scene=image_id if isinstance(image_id, int) else None)
                    self._log_locked("success", self._status["message"])
                return False
            if last_return_score >= threshold:
                text = self._ocr_text(self._ocr_lines(frame))
                if self._daily_yihuo_box_already_claimed_text(text):
                    with self._lock:
                        self._set_status_locked("running", f"日常_异火：#{image_id} 已领取态已命中", phase="daily_yihuo_wait_claimed", current_scene=image_id if isinstance(image_id, int) else None)
                        self._log_locked("success", self._status["message"])
                    return True
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=0.5)
        raise RuntimeError(f"日常_异火：等待 #{image_id}「箱子」或已领取态超时，最后箱子 {last_box_score:.0f}% 返回 {last_return_score:.0f}%")

    def _recover_daily_yihuo_local_pages(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        threshold: float = 80.0,
    ):
        ctx = runtime.ctx
        image259 = self._daily_yihuo_view_image(runtime, 259)
        image260 = self._daily_yihuo_view_image(runtime, 260)
        image261 = self._daily_yihuo_view_image(runtime, 261)
        pages: list[tuple[dict[str, Any], str]] = [
            (image261, "箱子"),
            (image260, "净莲"),
            (image259, "异火"),
        ]
        recovered = False
        for _ in range(3):
            if stop_event.is_set():
                raise InterruptedError()
            frame = self._screencap(ctx)
            matched: tuple[dict[str, Any], dict[str, Any], float] | None = None
            for image, identity_title in pages:
                identity_shape = self._find_shape(image, identity_title)
                if identity_shape is None:
                    continue
                score = float(self._shape_score(ctx, image, identity_shape, frame) or 0.0)
                if score >= threshold:
                    matched = (image, identity_shape, score)
                    break
            if matched is None:
                return recovered
            recovered = True
            image, _identity_shape, score = matched
            image_id = image.get("id") or _runtime_image_number(image) or "?"
            return_shape = self._find_shape(image, "返回")
            if return_shape is None:
                raise RuntimeError(f"日常_异火：检测到 #{image_id} 局部页 {score:.0f}%，但缺少「返回」标注")
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"日常_异火：从残留局部页 #{image_id} 点击「返回」",
                    phase="daily_yihuo_recover_local_page",
                    current_scene=image_id if isinstance(image_id, int) else None,
                )
                self._log_locked("action", self._status["message"])
            self._click_shape(ctx, image, return_shape, frame)
            yield from self._wait_runtime_action_settle(ctx, stop_event, seconds=1.0)
        return recovered
