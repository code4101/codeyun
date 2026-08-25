from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.runtime.mumu_control import (
    mark_mumu_device_startup_ready,
    mumu_device_health_check,
    recover_mumu_device,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    LOGIN_GAME_SCHEDULER_TASK_ID,
)


class LoginGameTaskMixin:
    login_game_scene_ids = (14, 15, 16, 17, 18, 19, 20, 21, 22, 34, 47, 49, 415, 546, 611, 661)
    login_action_scene_ids = frozenset({14, 15, 16, 17, 18, 415, 546, 661})
    # A healthy device and an arbitrary recognized game page do not prove that
    # login completed.  Keep this list explicit so newly recognized startup
    # overlays cannot silently turn a long unknown wait into false success.
    login_terminal_scene_ids = frozenset({19, 20, 21, 22, 34, 47, 49})

    @staticmethod
    def _resolve_login_scene(scene_id: int | None, frame_text: str) -> int | None:
        """Use only the formal scene matcher for login actions."""

        del frame_text
        return scene_id

    @staticmethod
    def _is_resource_loading_frame(frame_text: str) -> bool:
        compact = "".join(str(frame_text or "").split())
        return "AppVer" in compact and (
            "正在初始化资源" in compact
            or "初始化资源" in compact
            or "正在加载资源" in compact
        )

    @staticmethod
    def _visible_bubble_proves_game_ready(runtime: Any, *, frame: str) -> bool:
        """Accept the formal SDK bubble as direct post-login evidence.

        The bubble is an Android top-level overlay and can cover an arbitrary
        business page that is intentionally absent from the login candidate
        set.  A unique, fully resolved ``#421[气泡]`` match proves both that
        the game has left startup and that the post-login bubble invariant
        needs reconciliation.  A missing or ambiguous match proves nothing;
        the bounded unknown/loading protection remains in force.
        """

        # The Android bubble can already exist on the game cover.  The cover
        # gate must consume #18's formal scene identity, never the fixed
        # coordinate-only ``进入游戏`` action Shape.  Action Shapes authorize
        # a click only after their owning scene has been recognized; they are
        # not visual evidence in their own right.
        cover_matched, _cover_score, _cover_frame = runtime.match_view(
            18,
            frame_data_url=frame,
        )
        if cover_matched:
            return False
        match = runtime.shape_matches(421, "气泡", frame_data_url=frame)
        resolved = (match or {}).get("resolved_box") or (match or {}).get("fixed_box")
        return bool(
            match is not None
            and isinstance(resolved, dict)
            and bool(match.get("unique_match", True))
        )

    def _ensure_world_ready_via_login_game(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        """Defer scheduled business to login, retaining direct debug compatibility."""
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        scene_id, _score, frame = runtime.current_scene(self.login_game_scene_ids, update=True)
        frame_text = runtime.ocr_text(frame)
        scene_id = self._resolve_login_scene(scene_id, frame_text)
        resource_loading = scene_id is None and self._is_resource_loading_frame(frame_text)
        if scene_id not in self.login_action_scene_ids and not resource_loading:
            return False
        scheduler_task_id = str((payload or {}).get("__scheduler_task_id") or "")
        if scheduler_task_id and scheduler_task_id != LOGIN_GAME_SCHEDULER_TASK_ID:
            self._schedule_login_job_first()
            # This business Job was already consumed as a Scheduler attempt.
            # Give it a fresh, still-imminent timestamp so login stays first and
            # the business Job naturally retries after login without being
            # misclassified as a Cell that forgot its scheduling decision.
            retry_at = (datetime.now() + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
            self._persist_scheduler_task_next_time(scheduler_task_id, retry_at)
            self._log(
                "info",
                (
                    f"业务作业前置：检测到登录链 #{scene_id}，已将“登录”排到现有 next_time 队首"
                    if scene_id is not None
                    else "业务作业前置：检测到登录链，已将“登录”排到现有 next_time 队首"
                ),
            )
            return "scheduled"
        self._log(
            "info",
            (
                f"业务作业前置：调用标准“登录游戏”动作，当前 #{scene_id}"
                if scene_id is not None
                else "业务作业前置：调用标准“登录游戏”动作"
            ),
        )
        result = self._execute_login_game_task(ctx, stop_event, dict(payload or {}))
        if hasattr(result, "send"):
            yield from result
        return True

    def _execute_login_game_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        """Ensure the device is healthy and handle only formal login scenes."""
        payload = dict(payload or {})
        self._login_game_terminal_message = ""
        vmindex = str(payload.get("vmindex") or "1")
        loading_timeout = max(1.0, float(payload.get("loading_timeout_seconds") or 300.0))
        loading_poll = max(0.1, float(payload.get("loading_poll_seconds") or 2.0))
        health = mumu_device_health_check(vmindex=vmindex, force=True)
        device_started = str(health.get("status") or "") == "healthy"
        if not device_started:
            recovery = recover_mumu_device(
                vmindex=vmindex,
                reason="login_game_device_not_started",
            )
            device_started = str(recovery.get("status") or "") == "healthy"
            if not device_started:
                raise RuntimeError(f"登录游戏：模拟器未启动且标准恢复失败：{recovery}")
        asset_tree_path = ctx.get("asset_tree_path")
        runtime = self._fanxiu_runtime(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        loading_started_at: float | None = None
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene(self.login_game_scene_ids, update=True)
            frame_text = runtime.ocr_text(frame)
            scene_id = self._resolve_login_scene(scene_id, frame_text)
            bubble_ready = bool(
                scene_id is None
                and self._visible_bubble_proves_game_ready(runtime, frame=frame)
            )
            # During the explicit Login Job, an unrecognized frame is not
            # proof that the game is ready.  Resource initialization can be
            # visually stable (and OCR may return no tokens) while Android and
            # ADB remain perfectly healthy.  Keep the bounded startup wait for
            # every unknown frame; recognized non-login game scenes still
            # complete immediately without navigation.
            resource_loading = scene_id is None and not bubble_ready
            if resource_loading:
                current = time.monotonic()
                if loading_started_at is None:
                    loading_started_at = current
                elapsed = current - loading_started_at
                if elapsed < loading_timeout:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"登录游戏：等待游戏离开未识别启动画面，已等待 {elapsed:.0f}/{loading_timeout:.0f}s",
                            phase="login_game_loading",
                            current_scene=None,
                        )
                    yield from runtime.wait_action_settle(
                        min(loading_poll, max(0.1, loading_timeout - elapsed))
                    )
                    continue
                self._log(
                    "warning",
                    "登录游戏：资源初始化 5 分钟仍未完成，完整重启模拟器后重试",
                )
                recovery = recover_mumu_device(
                    vmindex=vmindex,
                    reason="login_game_loading_timeout",
                    force_restart=True,
                )
                if str(recovery.get("status") or "") != "healthy":
                    raise RuntimeError(f"登录游戏：加载超时且模拟器重启失败：{recovery}")
                runtime = self._fanxiu_runtime(
                    ctx,
                    asset_tree_path if isinstance(asset_tree_path, Path) else None,
                    stop_event=stop_event,
                )
                loading_started_at = None
                continue
            loading_started_at = None
            if scene_id == 611:
                # #611 is a full-screen XuTian promotion overlay, not a stable
                # business landing.  The universal lower-left return was
                # verified in real Runtime to close it directly to #34.
                result = runtime.goto_view(34)
                if hasattr(result, "send"):
                    yield from result
                continue
            # Only an explicitly modelled post-login terminal proves success.
            # Global popup candidates are handled inside current_scene() and
            # recognition then repeats until one of these terminals appears.
            if scene_id in self.login_terminal_scene_ids or bubble_ready:
                reason = (
                    f"login_game_scene_{scene_id}"
                    if scene_id in self.login_terminal_scene_ids
                    else "login_game_visible_bubble"
                )
                location = (
                    f"#{scene_id}"
                    if scene_id in self.login_terminal_scene_ids
                    else "#421 气泡覆盖的已登录业务页"
                )
                mark_mumu_device_startup_ready(reason=reason)
                bubble_outcome = ""
                bubble_reconcile = getattr(
                    self,
                    "_reconcile_bubble_after_login",
                    None,
                )
                if callable(bubble_reconcile):
                    reconcile_result = bubble_reconcile(
                        ctx, stop_event, payload
                    )
                    if hasattr(reconcile_result, "send"):
                        reconcile_result = yield from reconcile_result
                    mode = str((reconcile_result or {}).get("mode") or "")
                    if mode == "hidden_inline":
                        bubble_outcome = "气泡已隐藏"
                        self._log("info", "登录游戏：已同步确认气泡隐藏")
                    elif mode == "scheduled_weekly":
                        task_id = str((reconcile_result or {}).get("task_id") or "")
                        if task_id != "bubble-weekly-pills":
                            raise RuntimeError("登录游戏：气泡周事务没有落到唯一标准作业")
                        bubble_outcome = "气泡周事务已触发"
                        self._log(
                            "info",
                            f"登录游戏：本周气泡领取未闭环，已触发 {task_id}",
                        )
                    else:
                        raise RuntimeError("登录游戏：气泡协调返回了未知终态")
                else:
                    bubble_followup = getattr(
                    self,
                    "_schedule_bubble_reconcile_after_login",
                    None,
                    )
                    if callable(bubble_followup):
                        scheduled_task_id = bubble_followup(now=job_now())
                        if scheduled_task_id != "bubble-weekly-pills":
                            raise RuntimeError("登录游戏：气泡协调器没有返回唯一标准作业")
                        bubble_outcome = "气泡周事务已触发"
                        self._log(
                            "info",
                            f"登录游戏：已按本周气泡事实触发 {scheduled_task_id}",
                        )
                    else:
                        raise RuntimeError("登录游戏：缺少气泡协调后置能力")
                completion_message = f"登录游戏完成，已在 {location}；{bubble_outcome}"
                runtime.set_completion_message(completion_message)
                # The standard login job does not use the generic daily-task
                # wrapper that normally carries Runtime completion text back
                # into Scheduler state.  Preserve the verified terminal here
                # so its registration wrapper can return a truthful
                # ``last_message`` instead of the last loading-progress text.
                self._login_game_terminal_message = completion_message
                self._log(
                    "success",
                    f"登录游戏：设备已启动，{bubble_outcome}，保留 {location}",
                )
                return "success"
            if scene_id not in self.login_action_scene_ids:
                raise RuntimeError(
                    f"登录游戏：当前 #{scene_id} 不是已定义的登录终态，拒绝报告成功"
                )
            if scene_id in {415, 546}:
                self._raise_game_maintenance(
                    scene_id=scene_id,
                    evidence={"source": "login_game_scene", "scene_id": scene_id},
                )

            with self._lock:
                self._set_status_locked(
                    "running",
                    f"登录游戏：当前 #{scene_id} {score:.0f}%",
                    phase="login_game",
                    current_scene=scene_id,
                )

            if scene_id == 14:
                runtime.click_shape_center(14, "关闭公告")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 15:
                runtime.click_shape_center(15, "登录")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 16:
                raise RuntimeError("登录游戏：进入 #16 挑选账号；为避免误登，请人工选择账号后重新运行")
            if scene_id == 17:
                runtime.click_shape_center(17, "同意")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 18:
                runtime.click_shape_center(18, "进入游戏")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 661:
                runtime.click_shape_center(661, "进入")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            raise RuntimeError(f"登录游戏：暂不支持从 #{scene_id} 继续")
