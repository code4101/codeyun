from __future__ import annotations

import threading
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.client.mumu_control import (
    mark_mumu_device_startup_ready,
    mumu_device_health_check,
    mumu_device_startup_grace_state,
    recover_mumu_device,
)
from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import (
    LOGIN_GAME_SCHEDULER_TASK_ID,
)
from backend.core.fanxiu.data_annotation.login_recovery import (
    FanxiuLoginStalled,
    LoginProgress,
    loading_progress,
)


class LoginGameTaskMixin:
    login_game_scene_ids = (14, 15, 16, 17, 18, 19, 20, 21, 22, 34, 49, 415, 611, 661, 694, 695)
    login_action_scene_ids = frozenset({14, 15, 16, 17, 18, 415, 694, 695})
    # A healthy device and an arbitrary recognized game page do not prove that
    # login completed.  Keep this list explicit so newly recognized startup
    # overlays cannot silently turn a long unknown wait into false success.
    # #661 is the logged-in world with a landmark Enter button (alias of
    # #34). Clicking it opens gameplay, such as #400, rather than logging in.
    login_terminal_scene_ids = frozenset({19, 20, 21, 22, 34, 49, 661})

    @staticmethod
    def _resolve_login_scene(scene_id: int | None, frame_text: str) -> int | None:
        """Use only the formal scene matcher for login actions."""

        del frame_text
        return scene_id

    @staticmethod
    def _is_resource_loading_frame(frame_text: str) -> bool:
        compact = "".join(str(frame_text or "").split())
        # Adjacent OCR line boxes can duplicate the final character (化化).
        if "AppVer" not in compact:
            return False
        if re.search(r"(?:初始化化?|加载载?)资源", compact):
            return True
        # Bright startup art can erase or split 初始化 into 初始台化. The
        # version header plus numeric progress and the exact storage notice
        # independently prove this loading screen, authorizing only a wait.
        return "ResVer" in compact and bool(re.search(
            r"\d+(?:\.\d+)?%[（(]本次不会占用额外存储空间[）)]", compact))

    @staticmethod
    def _has_visible_login_bubble(context: Any, *, frame: str) -> bool:
        """Locate an SDK obstruction; its visibility never proves login.

        The same bubble appears on the announcement and cover before login.
        A unique match authorizes only hiding it and recognizing a fresh frame.
        """
        match = context.shape_matches(421, "气泡", frame_data_url=frame)
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
        context = self._behavior_tree_context(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        startup_gate = mumu_device_startup_grace_state()
        login_required = bool(startup_gate.get("login_required"))
        _wait_scene_match = yield from context.wait_scene(self.login_game_scene_ids, label='登录前置：识别当前场景', wait=5.0, required=False)
        (scene_id, _score, frame) = (
            (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
            if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
        )
        frame_text = context.ocr_text(frame)
        scene_id = self._resolve_login_scene(scene_id, frame_text)
        resource_loading = scene_id is None and self._is_resource_loading_frame(frame_text)
        if not login_required and scene_id not in self.login_action_scene_ids and not resource_loading:
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
        context = self._behavior_tree_context(
            ctx,
            asset_tree_path if isinstance(asset_tree_path, Path) else None,
            stop_event=stop_event,
        )
        progress = LoginProgress(timeout_seconds=loading_timeout)
        unknown_started_at: float | None = None
        unknown_bubble_hide_attempted = False
        action_attempt_counts: dict[int, int] = {}
        while True:
            self._raise_if_stopped(stop_event)
            # Login is also the post-restart cleanup transaction. Use the
            # canonical layered recognizer so ordinary popup nodes are handled
            # and recognition repeats before the login state machine proceeds.
            _wait_scene_match = yield from context.wait_scene(self.login_game_scene_ids, label='登录游戏：识别当前场景', wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            frame_text = context.ocr_text(frame)
            if scene_id is None:
                # Loading screens may have no scene asset. Scene-scoped OCR
                # is empty there, so it cannot prove resource initialization.
                # Full-frame text only authorizes the existing bounded wait.
                frame_text = " ".join(str(token.get("text") or "")
                                      for token in context.full_frame_ocr_tokens(frame))
            scene_id = self._resolve_login_scene(scene_id, frame_text)
            # Unknown and resource loading are different facts.  Only explicit
            # loading OCR may enter the bounded loading wait; an arbitrary
            # unknown frame must fail closed and must never authorize a MuMu
            # restart.
            resource_loading = (
                scene_id is None
                and self._is_resource_loading_frame(frame_text)
            )
            if scene_id is None and not resource_loading:
                # Fade/animation frames can temporarily lose loading text.
                # Observe for the same bounded unknown window as navigation;
                # Only a separately matched SDK obstruction may be hidden;
                # that action never resets this unknown-frame deadline.
                current = time.monotonic()
                if unknown_started_at is None:
                    unknown_started_at = current
                if current - unknown_started_at < 60.0:
                    if (
                        not unknown_bubble_hide_attempted
                        and self._has_visible_login_bubble(context, frame=frame)
                    ):
                        unknown_bubble_hide_attempted = True
                        self._log("info", "登录游戏：隐藏已定位的 SDK 气泡后重新识别；气泡不构成登录完成证据")
                        yield from self._ensure_bubble_hidden(ctx, stop_event, payload)
                        context.clear_frame()
                        continue
                    yield from context.wait_action_settle(loading_poll)
                    continue
                raise RuntimeError(
                    "登录游戏：当前画面未识别且无资源初始化证据；拒绝点击或重启模拟器"
                )
            unknown_started_at = None
            unknown_bubble_hide_attempted = False
            if resource_loading:
                current = time.monotonic()
                elapsed = progress.observe("resource_loading", current, loading_progress(frame_text))
                if elapsed < loading_timeout:
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"登录游戏：资源初始化无进展 {elapsed:.0f}/{loading_timeout:.0f}s，进度 {progress.high_water}",
                            phase="login_game_loading",
                            current_scene=None,
                        )
                    yield from context.wait_action_settle(
                        min(loading_poll, max(0.1, loading_timeout - elapsed))
                    )
                    continue
                raise FanxiuLoginStalled(
                    f"登录游戏：资源初始化连续 {elapsed:.0f}s 无进展，进度 {progress.high_water}；"
                    "结束当前 Cell 后执行有次数限制的游戏恢复"
                )
            if scene_id in {23, 611}:
                # World entry can expose the daily signup or XuTian promotion
                # page. Reconnection must close these through known asset
                # routes and re-observe a login terminal, never assume success.
                result = context.go_scene(34, known_paths_only=True)
                if hasattr(result, "send"):
                    yield from result
                continue
            # Only an explicitly modelled post-login terminal proves success.
            # Global popup candidates were handled by the layered recognizer,
            # which repeats until one of these terminals appears.
            if scene_id in self.login_terminal_scene_ids:
                reason = f"login_game_scene_{scene_id}"
                location = f"#{scene_id}"
                bubble_outcome = ""
                mode = ""
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
                        mode = "scheduled_weekly"
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
                        mode = "scheduled_weekly"
                        self._log(
                            "info",
                            f"登录游戏：已按本周气泡事实触发 {scheduled_task_id}",
                        )
                    else:
                        raise RuntimeError("登录游戏：缺少气泡协调后置能力")
                # Login and its mandatory bubble reconciliation form one
                # startup transaction.  Keep ordinary Scheduler work gated if
                # either half fails.
                if mode != "scheduled_weekly":
                    mark_mumu_device_startup_ready(reason=reason)
                completion_message = f"登录游戏完成，已在 {location}；{bubble_outcome}"
                context.set_completion_message(completion_message)
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
            if scene_id == 415:
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

            automated_action_scenes = {14, 17, 18, 694}
            if scene_id in automated_action_scenes:
                previous_phase = progress.phase
                elapsed = progress.observe(f"scene_{scene_id}", time.monotonic())
                if previous_phase != progress.phase:
                    action_attempt_counts.clear()
                if elapsed >= progress.timeout_seconds:
                    raise FanxiuLoginStalled(
                        f"登录游戏：#{scene_id} 连续 {elapsed:.0f}s 未离开；"
                        "结束当前 Cell 后执行有次数限制的游戏恢复"
                    )
                attempts = int(action_attempt_counts.get(scene_id) or 0)
                if attempts >= 2:
                    # A successful ADB tap does not prove delivery or VM failure.
                    # Stop hammering the button; let the same progress deadline
                    # decide when an app-only recovery is justified.
                    yield from context.wait_action_settle(loading_poll)
                    continue
                action_attempt_counts[scene_id] = attempts + 1

            if scene_id == 14:
                context.click_shape_center(14, "关闭公告")
                yield from context.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 15:
                from ..external_login_handoff import read_external_login_handoff
                handoff = read_external_login_handoff()
                if handoff.get("status") in {"waiting", "completed"} and not handoff["blocked"]:
                    # The user authorized reconnecting after the operator's
                    # handoff. Resume only the SDK's already selected account;
                    # never choose another account or enter credentials.
                    account_text = context.ocr_text_in_shapes(15, ["当前账号"], frame_data_url=frame)
                    accounts = re.findall(r"(?<!\d)1\d{10}(?!\d)|1\d{2}\*{4,}\d{4}", account_text)
                    if len(accounts) != 1:
                        raise RuntimeError("登录游戏：交接后未能证明已保存的单一账号，保留现场等待人工登录")
                    if context.shape_matches(15, "未勾选协议", frame_data_url=frame):
                        context.click_shape_center(15, "未勾选协议")
                        yield from context.wait_action_settle(0.5)
                    context.click_shape_center(15, "登录")
                    yield from context.wait_action_settle(loading_poll)
                    continue
                raise RuntimeError("登录游戏：进入 #15 账号登录；凭据与登录确认只能由用户手动操作")
            if scene_id == 16:
                raise RuntimeError("登录游戏：进入 #16 挑选账号；为避免误登，请人工选择账号后重新运行")
            if scene_id == 17:
                context.click_shape_center(17, "同意")
                yield from context.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 694:
                context.click_shape_center(694, "确认")
                yield from context.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 695:
                raise RuntimeError("登录游戏：进入 #695 账号登录；凭据与登录确认只能由用户手动操作")
            if scene_id == 18:
                context.click_shape_center(18, "进入游戏")
                yield from context.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            raise RuntimeError(f"登录游戏：暂不支持从 #{scene_id} 继续")
