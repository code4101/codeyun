from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pyxllib.prog import BehaviorTreeStatus
from pyxllib.autogui import ActionPlanner, Shape, View, image_number as _runtime_image_number

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text
from backend.core.fanxiu.data_annotation import runtime_runner as _runtime_runner
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.data_annotation.runtime_runner import (
    FULLWIDTH_DIGIT_TRANSLATION,
    _now,
    _parse_daily_boss_cd_seconds,
    _parse_daily_boss_reward_remaining,
    _parse_first_int,
    _parse_xianfu_skill_cd_seconds,
    _parse_xianfu_visit_cd_seconds,
)


class XianfuTaskMixin:
    _XIANFU_INTERNAL_SCENES = {171, 172, 173, 174, 175, 176, 177, 185}

    def _fallback_xianfu_scene_from_status(
        self,
        scene_id: int | None,
        current_text: str,
        *,
        task_label: str,
    ) -> int | None:
        if scene_id is not None:
            return scene_id
        if not self._xianfu_home_text_is_scene(current_text):
            return None
        with self._lock:
            raw_scene = self._status.get("current_scene")
        try:
            status_scene = int(raw_scene)
        except (TypeError, ValueError):
            return None
        if status_scene in self._XIANFU_INTERNAL_SCENES:
            self._log("warning", f"{task_label}：即时识别 unknown，沿用 Runtime 当前仙府场景 #{status_scene}")
            return status_scene
        return None

    def _advance_xianfu_cutscene_to_home(self, runtime: FanxiuRuntime, *, task_label: str):
        view185 = runtime.get_view(185)
        skip_shape = view185.get_shape("跳过") if isinstance(view185, View) else None
        if skip_shape is None:
            raise RuntimeError(f"{task_label}：缺少 #185「跳过」标注，无法跳过仙府过场")
        for attempt in range(5):
            with self._lock:
                self._set_status_locked("running", f"{task_label}：跳过仙府过场", phase="xianfu_cutscene_skip", current_scene=185)
                self._log_locked("action", f"{task_label}：点击 #185「跳过」")
            skip_shape.click(runtime)
            yield from runtime.wait_action_settle(1.5)
            start = time.monotonic()
            last_scene_id: int | None = 185
            last_score = 0.0
            while time.monotonic() - start < 6.0:
                scene_id, score, frame = runtime.current_scene([171, 185], update=True)
                last_scene_id, last_score = scene_id, score
                text = runtime.ocr_text(frame)
                if scene_id == 171 or self._xianfu_home_text_is_scene(text):
                    with self._lock:
                        self._set_status_locked("running", f"{task_label}：已到仙府主页 #171", phase="xianfu_cutscene_done", current_scene=171)
                    self._log("success", f"{task_label}：已跳过仙府过场，进入 #171")
                    return "success"
                if scene_id != 185:
                    break
                yield BehaviorTreeStatus.RUNNING
            if attempt < 4:
                self._log("warning", f"{task_label}：点击跳过后仍在 #{last_scene_id or 'unknown'} {last_score:.0f}%，重试跳过")
                continue
            raise TimeoutError(f"{task_label}：跳过仙府过场后仍未到 #171，最后 #{last_scene_id or 'unknown'} {last_score:.0f}%")
        return "success"

    def _ensure_xianfu_home_partner_tab(self, runtime: FanxiuRuntime, image171: dict[str, Any], *, task_label: str):
        frame = runtime.cur_frame(update=True)
        full_text = _sanitize_ocr_text(runtime.ocr_text(frame))
        if self._xianfu_partner_entry_ready_text(full_text):
            return "success"
        raise RuntimeError(f"{task_label}：#171 未显示「寻仙台」入口，请检查仙府主页标注或当前页状态；当前 OCR={full_text or '空'}")

    def _xianfu_partner_entry_ready_text(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "寻仙台" in normalized

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
        scene_id, score, _frame = runtime.current_scene([177, 176, 175, 174, 173, 172, 185, 171, 69, 34], update=True)
        current_text = runtime.ocr_text(_frame)
        if scene_id is None:
            if self._xianfu_visit_text_is_continue_popup(current_text):
                scene_id = 175
            elif self._xianfu_visit_text_is_juepin(current_text):
                scene_id = 174
            elif self._xianfu_home_text_is_scene(current_text):
                scene_id = 171
        elif scene_id == 34 and self._xianfu_home_text_is_scene(current_text):
            self._log("warning", "仙府_寻访仙侣：当前画面 OCR 命中仙府主页，覆盖 #34 误识别为 #171")
            scene_id = 171
        scene_id = self._fallback_xianfu_scene_from_status(scene_id, current_text, task_label="仙府_寻访仙侣")
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})

        if scene_id in {177, 176}:
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_寻访仙侣：起点停在领悟绝技页 #{scene_id}，先返回世界 #34",
                    phase="xianfu_visit_restore_from_skill",
                    current_scene=scene_id,
                )
                self._log_locked("warning", f"仙府_寻访仙侣：起点停在领悟绝技页 #{scene_id}，先走领悟绝技收尾链路")
            if scene_id == 177:
                yield from self._handle_xianfu_learn_skill_result_popup(runtime)
            yield from self._return_xianfu_learn_skill_to_world(runtime)
            scene_id = 34

        if scene_id == 185:
            yield from self._advance_xianfu_cutscene_to_home(runtime, task_label="仙府_寻访仙侣")
            scene_id = 171

        if scene_id == 69:
            raise RuntimeError("仙府_寻访仙侣：当前停在日常页 #69，禁止把日常页退出路径作为仙府寻访入口；请先由日常作业收尾回世界 #34 后再重试")

        if scene_id == 175:
            yield from self._handle_xianfu_continue_visit_popup(runtime, max_continue=max_continue)
            scene_id = 174

        if scene_id != 174:
            if scene_id not in {171, 172, 173}:
                if scene_id == 34:
                    tree = ctx.get("asset_tree")
                    if isinstance(tree, list) and self._find_scene_route(tree, 34, 171) is None:
                        raise RuntimeError("仙府_寻访仙侣：缺少 #34「仙府」入口标注或 sceneJumpTarget=171，无法从世界进入仙府主页 #171")
                with self._lock:
                    self._set_status_locked("running", "仙府_寻访仙侣：进入仙府主页 #171", phase="xianfu_visit_go_home")
                    self._log_locked("action", "仙府_寻访仙侣：按场景图跳转到 #171")
                yield from runtime.goto_view(171, layer0_wait_seconds=60.0)
                scene_id = 171
            if scene_id == 171:
                view171 = runtime.get_view(171)
                image171 = ctx.get("images", {}).get(171)
                if isinstance(image171, dict):
                    self._ensure_xianfu_home_partner_tab(runtime, image171, task_label="仙府_寻访仙侣")
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
                yield from self._wait_xianfu_visit_juepin(runtime, timeout=18.0, label="仙府_寻访仙侣：等待绝品仙侣 #174")

        image174 = ctx.get("images", {}).get(174)
        if not isinstance(image174, dict):
            raise RuntimeError("缺少 #174 绝品仙侣标注，无法读取寻访状态")
        frame = runtime.cur_frame(update=True)
        status_text = self._fanxiu_runtime_ocr_text_in_shapes(runtime, image174, ("状态", "免费提示"), frame_data_url=frame, padding=16)
        cd_seconds = _parse_xianfu_visit_cd_seconds(status_text)
        if cd_seconds is None:
            raise RuntimeError(f"仙府_寻访仙侣：无法识别免费寻访倒计时：{status_text or '空'}")
        if cd_seconds > 0:
            next_time = (_runtime_runner._now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-visit-partner")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_visit_partner",
                label="仙府_寻访仙侣",
                last_result="success",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_寻访仙侣：未到免费时间，{status_text}，下次 {next_time}",
                    phase="xianfu_visit_wait_cd",
                    current_scene=174,
                )
                self._log_locked("success", self._status["message"])
            yield from self._return_xianfu_visit_partner_to_world(runtime)
            return "success"

        image175 = ctx.get("images", {}).get(175)
        if not isinstance(image175, dict):
            self._log("skip", "仙府_寻访仙侣：当前可免费寻访，但缺少 #175「继续寻访」弹窗标注，暂不自动点击")
            yield from self._return_xianfu_visit_partner_to_world(runtime)
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
        status_text = self._fanxiu_runtime_ocr_text_in_shapes(runtime, image174, ("状态", "免费提示"), frame_data_url=frame, padding=16)
        cd_seconds = _parse_xianfu_visit_cd_seconds(status_text)
        if cd_seconds and cd_seconds > 0:
            next_time = (_runtime_runner._now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-visit-partner")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_visit_partner",
                label="仙府_寻访仙侣",
                last_result="success",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_寻访仙侣：寻访后读取 CD {status_text}，下次 {next_time}",
                    phase="xianfu_visit_done",
                    current_scene=174,
                )
                self._log_locked("success", self._status["message"])
            yield from self._return_xianfu_visit_partner_to_world(runtime)
            return "success"
        self._log("skip", f"仙府_寻访仙侣：寻访后未读到有效 CD：{status_text or '空'}")
        yield from self._return_xianfu_visit_partner_to_world(runtime)
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
            half_text = self._fanxiu_runtime_ocr_text_in_shapes(runtime, view175, ("半价",), frame_data_url=frame, padding=24)
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
        for close_attempt in range(3):
            with self._lock:
                self._set_status_locked("running", "仙府_寻访仙侣：关闭继续寻访弹窗", phase="xianfu_visit_close_continue", current_scene=175)
                self._log_locked("action", "仙府_寻访仙侣：点击 #175「关闭」")
            close_shape.click(runtime)
            yield from runtime.wait_action_settle(1.0)
            scene_id, _score, frame = runtime.current_scene([174, 175], update=True)
            text = runtime.ocr_text(frame)
            if scene_id == 174 or self._xianfu_visit_text_is_juepin(text):
                return "success"
            if scene_id == 175 or self._xianfu_visit_text_is_continue_popup(text):
                if close_attempt < 2:
                    self._log("warning", "仙府_寻访仙侣：关闭后仍停在继续寻访弹窗，重试关闭")
                    continue
            break
        yield from self._wait_xianfu_visit_juepin(runtime, timeout=18.0, label="仙府_寻访仙侣：关闭弹窗后回到 #174")
        return "success"

    def _xianfu_visit_text_is_juepin(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "仙侣预览" in normalized and "寻访" in normalized and ("免费抽取" in normalized or "绝品仙侣" in normalized)

    def _xianfu_visit_text_is_continue_popup(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "继续寻访" in normalized and "关闭" in normalized

    def _xianfu_leave_confirm_text_is_scene(self, text: str) -> bool:
        normalized = _sanitize_ocr_text(text)
        return "离开当前场景" in normalized and "确认" in normalized and "取消" in normalized

    def _confirm_xianfu_leave_to_world(self, runtime: FanxiuRuntime, *, task_label: str):
        view86 = runtime.get_view(86)
        confirm_shape = view86.get_shape("确认") if isinstance(view86, View) else None
        if confirm_shape is None:
            raise RuntimeError(f"{task_label}：当前在 #86 离开确认弹窗，但缺少 #86「确认」标注，无法返回世界")
        with self._lock:
            self._set_status_locked("running", f"{task_label}：确认离开当前场景", phase="xianfu_return_confirm_leave", current_scene=86)
            self._log_locked("action", f"{task_label}：点击 #86「确认」")
        confirm_shape.click(runtime)
        yield from runtime.wait_view(34, timeout=30.0, label=f"{task_label}：确认离开后等待世界 #34")
        return "success"

    def _wait_xianfu_visit_juepin(self, runtime: FanxiuRuntime, *, timeout: float, label: str):
        return (yield from runtime.wait_any(
            {
                "scene": runtime.view_visible(174),
                "text": runtime.ocr_matches(self._xianfu_visit_text_is_juepin, label=f"{label} OCR"),
            },
            timeout=timeout,
            label=label,
        ))

    def _return_xianfu_visit_partner_to_world(self, runtime: FanxiuRuntime):
        with self._lock:
            self._set_status_locked("running", "仙府_寻访仙侣：返回世界 #34", phase="xianfu_visit_return_world")
            self._log_locked("action", "仙府_寻访仙侣：按仙府收尾链路返回 #34")
        yield from self._return_xianfu_pages_to_world(
            runtime,
            task_label="仙府_寻访仙侣",
            current_candidates=(175, 174, 173, 172, 171, 86, 34),
        )
        return "success"

    def _return_xianfu_pages_to_world(
        self,
        runtime: FanxiuRuntime,
        *,
        task_label: str,
        current_candidates: tuple[int, ...] = (177, 176, 175, 174, 173, 172, 171, 86, 34),
    ):
        for _attempt in range(6):
            scene_id, score, _frame = runtime.current_scene(current_candidates, update=True)
            text = runtime.ocr_text(_frame)
            if scene_id is None:
                if self._xianfu_visit_text_is_continue_popup(text):
                    scene_id = 175
                elif self._xianfu_visit_text_is_juepin(text):
                    scene_id = 174
                elif self._xianfu_leave_confirm_text_is_scene(text):
                    scene_id = 86
                elif self._daily_assistant_text_is_world_like(text):
                    scene_id = 34
            elif scene_id == 34 and self._xianfu_leave_confirm_text_is_scene(text):
                scene_id = 86
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})
            if scene_id == 34:
                self._log("success", f"{task_label}：已返回世界 #34")
                return "success"
            if scene_id == 86:
                yield from self._confirm_xianfu_leave_to_world(runtime, task_label=task_label)
                continue
            if scene_id == 177:
                view177 = runtime.get_view(177)
                continue_shape = view177.get_shape("继续") if isinstance(view177, View) else None
                if continue_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #177「继续」标注，无法关闭领悟结果弹窗")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭领悟结果弹窗", phase="xianfu_return_close_skill_result", current_scene=177)
                    self._log_locked("action", f"{task_label}：点击 #177「继续」")
                continue_shape.click(runtime)
                yield from runtime.wait_view(176, 171, 34, timeout=18.0, label=f"{task_label}：关闭 #177 后等待绝技页")
                continue
            if scene_id == 176:
                view176 = runtime.get_view(176)
                exit_shape = view176.get_shape("退出") if isinstance(view176, View) else None
                if exit_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #176「退出」标注，无法离开绝技页")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：退出绝技页", phase="xianfu_return_exit_skill", current_scene=176)
                    self._log_locked("action", f"{task_label}：点击 #176「退出」")
                exit_shape.click(runtime)
                yield from runtime.wait_view(171, 172, 34, timeout=18.0, label=f"{task_label}：退出 #176 后等待仙府页")
                continue
            if scene_id == 175:
                view175 = runtime.get_view(175)
                close_shape = view175.get_shape("关闭") if isinstance(view175, View) else None
                if close_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #175「关闭」标注，无法关闭寻访结果弹窗")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：关闭寻访结果弹窗", phase="xianfu_return_close_visit_result", current_scene=175)
                    self._log_locked("action", f"{task_label}：点击 #175「关闭」")
                close_shape.click(runtime)
                yield from runtime.wait_view(174, 173, 171, 34, timeout=18.0, label=f"{task_label}：关闭 #175 后等待仙府页")
                continue
            if scene_id == 174:
                view174 = runtime.get_view(174)
                exit_shape = view174.get_shape("退出") if isinstance(view174, View) else None
                if exit_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #174「退出」标注，无法离开绝品仙侣页")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：退出绝品仙侣页", phase="xianfu_return_exit_juepin", current_scene=174)
                    self._log_locked("action", f"{task_label}：点击 #174「退出」")
                exit_shape.click(runtime)
                yield from runtime.wait_view(171, 173, 172, 34, timeout=18.0, label=f"{task_label}：退出 #174 后等待仙府页")
                continue
            if scene_id == 173:
                view173 = runtime.get_view(173)
                back_shape = view173.get_shape("返回") if isinstance(view173, View) else None
                if back_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #173「返回」标注，无法离开仙侣寻访页")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：返回仙府主页", phase="xianfu_return_from_visit", current_scene=173)
                    self._log_locked("action", f"{task_label}：点击 #173「返回」")
                back_shape.click(runtime)
                yield from runtime.wait_view(171, 172, 34, timeout=18.0, label=f"{task_label}：返回 #173 后等待仙府主页")
                continue
            if scene_id == 171:
                view171 = runtime.get_view(171)
                leave_shape = view171.get_shape("离开") if isinstance(view171, View) else None
                if leave_shape is None:
                    raise RuntimeError(f"{task_label}：缺少 #171「离开」标注，无法返回世界")
                with self._lock:
                    self._set_status_locked("running", f"{task_label}：离开仙府", phase="xianfu_return_leave_home", current_scene=171)
                    self._log_locked("action", f"{task_label}：点击 #171「离开」")
                leave_shape.click(runtime)
                leave_result = yield from runtime.wait_view(86, 34, timeout=30.0, label=f"{task_label}：离开仙府后等待世界 #34")
                leave_scene_id = leave_result.id if isinstance(leave_result, View) else None
                if leave_scene_id == 86:
                    yield from self._confirm_xianfu_leave_to_world(runtime, task_label=task_label)
                continue
            if scene_id == 172:
                self._log("warning", f"{task_label}：停在 #172 寻仙台，回退通用场景图返回 #34")
                yield from runtime.goto_view(34)
                continue
            self._log("warning", f"{task_label}：当前场景 unknown/{score:.0f}%，回退通用场景图返回 #34")
            yield from runtime.goto_view(34)
        raise RuntimeError(f"{task_label}：返回世界 #34 未完成，不能按成功处理")

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
        preferred = [177, 176, 172, 185, 171, 34]
        scene_id, score, _frame = runtime.current_scene(preferred, update=True)
        current_text = runtime.ocr_text(_frame)
        if scene_id == 34 and self._xianfu_home_text_is_scene(current_text):
            self._log("warning", "仙府_领悟绝技：当前画面 OCR 命中仙府主页，覆盖 #34 误识别为 #171")
            scene_id = 171
        elif scene_id is None and self._xianfu_home_text_is_scene(current_text):
            scene_id = 171
        scene_id = self._fallback_xianfu_scene_from_status(scene_id, current_text, task_label="仙府_领悟绝技")
        if scene_id is not None:
            with self._lock:
                self._status.update({"current_scene": scene_id, "updated_at": time.time()})

        if scene_id == 177:
            yield from self._handle_xianfu_learn_skill_result_popup(runtime)
            scene_id = 176

        if scene_id == 185:
            yield from self._advance_xianfu_cutscene_to_home(runtime, task_label="仙府_领悟绝技")
            scene_id = 171

        if scene_id != 176:
            if scene_id not in {171, 172}:
                with self._lock:
                    self._set_status_locked("running", "仙府_领悟绝技：进入仙府主页 #171", phase="xianfu_skill_go_home")
                    self._log_locked("action", "仙府_领悟绝技：按场景图跳转到 #171")
                yield from runtime.goto_view(171, layer0_wait_seconds=60.0)
                scene_id = 171
            if scene_id == 171:
                view171 = runtime.get_view(171)
                image171 = images.get(171)
                if isinstance(image171, dict):
                    yield from self._ensure_xianfu_home_partner_tab(runtime, image171, task_label="仙府_领悟绝技")
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
        status_text = self._fanxiu_runtime_ocr_text_in_shapes(runtime, image176, ("状态", "价格"), frame_data_url=frame, padding=16)
        cd_seconds = _parse_xianfu_skill_cd_seconds(status_text)
        if cd_seconds is None:
            fallback_seconds = int(payload.get("fallback_seconds") or 1800)
            next_time = (_runtime_runner._now() + timedelta(seconds=max(60, fallback_seconds))).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
            )
            self._log("skip", f"仙府_领悟绝技：未识别到免费领悟或倒计时，当前文本：{status_text or '空'}；{next_time} 兜底重试")
            yield from self._return_xianfu_learn_skill_to_world(runtime)
            return "skipped"
        if cd_seconds > 0:
            next_time = (_runtime_runner._now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_retry_after(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
                last_result="skipped",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_领悟绝技：未到免费时间，{status_text}，下次 {next_time}；本轮未点击领悟",
                    phase="xianfu_skill_wait_cd",
                    current_scene=176,
                )
                self._log_locked("skip", self._status["message"])
            yield from self._return_xianfu_learn_skill_to_world(runtime)
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
        # The current tick has already OCR-confirmed the free-draw state.  Do not
        # run an independent shape match here: it duplicates OCR work and can
        # reject the same frame that produced the business decision above.
        runtime.click_shape_center(view176, learn_shape)
        yield from self._handle_xianfu_learn_skill_result_popup(runtime)

        frame = runtime.cur_frame(update=True)
        status_text = self._fanxiu_runtime_ocr_text_in_shapes(runtime, image176, ("状态", "价格"), frame_data_url=frame, padding=16)
        cd_seconds = _parse_xianfu_skill_cd_seconds(status_text)
        if cd_seconds and cd_seconds > 0:
            next_time = (_runtime_runner._now() + timedelta(seconds=cd_seconds)).strftime("%Y-%m-%d %H:%M:%S")
            scheduler_task_id = str(payload.get("__scheduler_task_id") or "xianfu-learn-skill")
            self._record_scheduler_task_discovered_next_time(
                scheduler_task_id,
                next_time,
                task_type="xianfu_learn_skill",
                label="仙府_领悟绝技",
                last_result="success",
            )
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"仙府_领悟绝技：领悟后读取 CD {status_text}，下次 {next_time}",
                    phase="xianfu_skill_done",
                    current_scene=176,
                )
                self._log_locked("success", self._status["message"])
            yield from self._return_xianfu_learn_skill_to_world(runtime)
            return "success"
        self._log("skip", f"仙府_领悟绝技：领悟后未读到有效 CD：{status_text or '空'}")
        yield from self._return_xianfu_learn_skill_to_world(runtime)
        return "skipped"
