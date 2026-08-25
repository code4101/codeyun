from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

from backend.core.fanxiu.data_annotation.maintenance import (
    MAINTENANCE_PROBE_DURATION_SECONDS,
    MAINTENANCE_PROBE_INTERVAL_SECONDS,
    MAINTENANCE_RECOVERY_TASK_ID,
    LOGIN_MAINTENANCE_PROMPT_SCENE_ID,
    GAME_STARTUP_POLL_SECONDS,
    GAME_STARTUP_TIMEOUT_SECONDS,
    FanxiuMaintenanceDetected,
    clear_maintenance_gate,
    maintenance_check_time_text,
    infer_game_startup_scene,
    open_maintenance_gate,
    read_maintenance_gate,
)
from backend.core.fanxiu.runtime.mumu_control import (
    mark_mumu_device_startup_ready,
    mumu_device_health_check,
    recover_mumu_device,
)


class MaintenanceTaskMixin:
    """Own the global maintenance gate and its single recovery job."""

    maintenance_probe_scene_ids = (14, 15, 16, 17, 18, 19, 20, 21, 22, 34, 47, 415, 546)
    maintenance_recovered_scene_ids = frozenset({19, 20, 21, 22, 34, 47, 49})

    @staticmethod
    def _maintenance_scene_proves_available(scene_id: int | None) -> bool:
        return scene_id is not None and scene_id not in {14, 15, 16, 17, 18, 415, 546}

    def _observe_maintenance_scene(self, runtime: Any, *, update: bool = True):
        """Observe startup state without invoking the ordinary-job maintenance guard."""

        if hasattr(runtime, "ctx") and hasattr(runtime, "cur_frame"):
            frame = runtime.cur_frame(update=update)
            candidate_ids = list(self.maintenance_probe_scene_ids)
            candidate_provider = getattr(self, "_runtime_scene_candidate_ids", None)
            if callable(candidate_provider):
                candidate_ids = list(dict.fromkeys([
                    *candidate_ids,
                    *candidate_provider(runtime.ctx),
                ]))
            scene_id, score = self._identify_scene_number(
                runtime.ctx,
                frame,
                candidate_ids,
            )
        else:
            # Lightweight test/runtime adapters may expose only the public view.
            scene_id, score, frame = runtime.current_scene(
                self.maintenance_probe_scene_ids,
                update=update,
            )
        text = runtime.ocr_text(frame)
        return infer_game_startup_scene(scene_id, text), score, frame, text

    def _maintenance_world_facts_path(self):
        from backend.core.fanxiu.behavior_tree.runtime import fanxiu_data_annotation_world_facts_path

        return fanxiu_data_annotation_world_facts_path()

    def _defer_maintenance_recovery(
        self,
        *,
        scene_id: int | None = 415,
        evidence: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> str:
        current = observed_at or datetime.now()
        open_maintenance_gate(
            self._maintenance_world_facts_path(),
            observed_at=current,
            scene_id=scene_id,
            evidence=evidence,
        )
        next_time = maintenance_check_time_text(current)
        self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, next_time)
        return next_time

    def _raise_game_maintenance(
        self,
        *,
        scene_id: int | None = 415,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        next_time = self._defer_maintenance_recovery(scene_id=scene_id, evidence=evidence)
        message = f"检测到游戏维护；普通作业已休眠，{next_time} 完整重启模拟器后复查"
        self._log("warning", message)
        raise FanxiuMaintenanceDetected(message, evidence=evidence)

    def _wait_for_game_startup_page(
        self,
        runtime: Any,
        stop_event: threading.Event,
        *,
        timeout: float = GAME_STARTUP_TIMEOUT_SECONDS,
        poll_seconds: float = GAME_STARTUP_POLL_SECONDS,
    ):
        """Wait until loading reaches an actionable login/startup page."""

        timeout = max(1.0, float(timeout))
        poll_seconds = max(0.1, float(poll_seconds))
        started_at = time.monotonic()
        last_scene_id: int | None = None
        last_text = ""
        while True:
            self._raise_if_stopped(stop_event)
            scene_id, score, frame, last_text = self._observe_maintenance_scene(runtime, update=True)
            last_scene_id = scene_id
            if scene_id in {14, 18, 415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID} or self._maintenance_scene_proves_available(scene_id):
                return {
                    "ready": True,
                    "scene_id": scene_id,
                    "score": score,
                    "frame": frame,
                    "ocr": last_text,
                    "elapsed_seconds": time.monotonic() - started_at,
                }
            elapsed = time.monotonic() - started_at
            if elapsed >= timeout:
                return {
                    "ready": False,
                    "scene_id": last_scene_id,
                    "score": score,
                    "frame": frame,
                    "ocr": last_text,
                    "elapsed_seconds": elapsed,
                }
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"维护复查：等待游戏启动页，已等待 {elapsed:.0f}/{timeout:.0f}s",
                    phase="maintenance_wait_startup",
                    current_scene=scene_id,
                )
            yield from runtime.wait_action_settle(min(poll_seconds, max(0.1, timeout - elapsed)))

    def _execute_maintenance_recovery_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        gate = read_maintenance_gate(self._maintenance_world_facts_path())
        if not gate.get("active"):
            self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
            return {"result": "success", "message": "维护门闩未开启，无需恢复检查"}

        runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        scene_id, score, frame, _text = self._observe_maintenance_scene(runtime, update=True)
        if scene_id == 34:
            clear_maintenance_gate(
                self._maintenance_world_facts_path(),
                evidence={"stage": "preflight", "scene_id": 34, "score": score},
            )
            self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
            runtime.set_completion_message("维护已结束；当前已在 #34 世界，无需重启模拟器")
            return {"result": "success", "message": "维护已结束，当前已在 #34 世界"}

        if self._maintenance_scene_proves_available(scene_id):
            mark_mumu_device_startup_ready(reason="maintenance_business_scene_seen")
            clear_maintenance_gate(
                self._maintenance_world_facts_path(),
                evidence={"stage": "business_scene_available", "scene_id": scene_id, "score": score},
            )
            self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
            runtime.set_completion_message(f"维护已结束；正式业务场景 #{scene_id} 可用")
            return {"result": "success", "message": f"维护已结束，正式业务场景 #{scene_id} 可用"}

        probe_interval = max(
            1.0,
            float(payload.get("probe_interval_seconds") or MAINTENANCE_PROBE_INTERVAL_SECONDS),
        )
        probe_duration = max(
            probe_interval,
            float(payload.get("probe_duration_seconds") or MAINTENANCE_PROBE_DURATION_SECONDS),
        )
        probe_attempts = max(1, int(probe_duration // probe_interval))

        # Announcement, cover and maintenance pages are already healthy,
        # actionable startup states. Probe them in place before considering a
        # restart. The cover control can look disabled while still accepting
        # input, so only action -> formal successor proves availability.
        if scene_id in {14, 18, 415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID}:
            for _ in range(8):
                self._raise_if_stopped(stop_event)
                if scene_id != 14:
                    break
                runtime.click_shape_center(14, "关闭公告")
                yield from runtime.wait_action_settle(2.0)
                scene_id, _score, frame, _text = self._observe_maintenance_scene(runtime, update=True)

            if scene_id == 18:
                for attempt in range(1, probe_attempts + 1):
                    self._raise_if_stopped(stop_event)
                    runtime.click_shape_center(18, "进入游戏")
                    with self._lock:
                        self._set_status_locked(
                            "running",
                            f"维护轻量复查：第 {attempt}/{probe_attempts} 次实际点击进入游戏",
                            phase="maintenance_probe",
                            current_scene=18,
                        )
                    yield from runtime.wait_action_settle(probe_interval)
                    scene_id, _score, frame, _text = self._observe_maintenance_scene(runtime, update=True)
                    if self._maintenance_scene_proves_available(scene_id):
                        break
                    if scene_id in {415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID}:
                        break

            if not self._maintenance_scene_proves_available(scene_id):
                next_time = self._defer_maintenance_recovery(
                    scene_id=scene_id,
                    evidence={
                        "stage": "lightweight_enter_game_probe",
                        "scene_id": scene_id,
                        "attempts": probe_attempts if scene_id in {18, None} else 0,
                        "duration_seconds": probe_duration,
                        "ocr": runtime.ocr_text(frame)[:160],
                    },
                )
                return {
                    "result": "success",
                    "message": f"轻量点击未观察到正式登录后场景；不重启模拟器，{next_time} 再试",
                }

            if scene_id != 34:
                mark_mumu_device_startup_ready(reason="maintenance_startup_page_seen")
                clear_maintenance_gate(
                    self._maintenance_world_facts_path(),
                    evidence={"stage": "login_scheduled", "scene_id": scene_id},
                )
                self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
                self._schedule_login_job_first()
                return {
                    "result": "success",
                    "message": "维护已结束；实际点击已进入登录后场景并将“登录”排到队首",
                }

            clear_maintenance_gate(
                self._maintenance_world_facts_path(),
                evidence={"stage": "recovered", "scene_id": 34},
            )
            self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
            runtime.set_completion_message("维护结束，轻量点击已进入 #34 世界；普通作业恢复调度")
            self._log("success", "维护恢复：轻量点击已确认进入 #34 世界，解除维护门闩")
            return {"result": "success", "message": "维护结束，已进入 #34 世界"}

        health = mumu_device_health_check(
            vmindex=str(payload.get("vmindex") or "1"),
            force=True,
        )
        if str(health.get("status") or "") == "healthy":
            next_time = self._defer_maintenance_recovery(
                scene_id=scene_id,
                evidence={
                    "stage": "healthy_unrecognized_scene",
                    "scene_id": scene_id,
                    "health": {"status": health.get("status")},
                },
            )
            return {
                "result": "success",
                "message": f"设备健康但当前场景未识别；不重启模拟器，{next_time} 再轻量复查",
            }

        # Reserve the next maintenance wake time before doing any blocking emulator work.
        # If this attempt errors or is interrupted, its overdue timestamp must
        # not cause the external dispatcher to restart MuMu in a tight loop.
        self._persist_scheduler_task_next_time(
            MAINTENANCE_RECOVERY_TASK_ID,
            maintenance_check_time_text(),
        )

        startup_timeout = max(
            1.0,
            float(payload.get("startup_timeout_seconds") or GAME_STARTUP_TIMEOUT_SECONDS),
        )
        startup_poll = max(
            0.1,
            float(payload.get("startup_poll_seconds") or GAME_STARTUP_POLL_SECONDS),
        )
        startup: dict[str, Any] = {}
        restart_count = 0
        while True:
            self._raise_if_stopped(stop_event)
            restart_count += 1
            with self._lock:
                self._set_status_locked(
                    "running",
                    (
                        f"维护复查：完整重启 MuMu 模拟器"
                        f"（第 {restart_count} 次）"
                    ),
                    phase="maintenance_restart",
                )
            recovery = recover_mumu_device(
                vmindex=str(payload.get("vmindex") or "1"),
                reason="game_maintenance_wake",
                force_restart=True,
            )
            if not recovery.get("recovered") or str(recovery.get("status") or "") != "healthy":
                next_time = self._defer_maintenance_recovery(
                    scene_id=gate.get("scene_id"),
                    evidence={"stage": "restart", "recovery": recovery},
                )
                return {
                    "result": "success",
                    "message": f"维护复查：模拟器重启未就绪，休眠至 {next_time}",
                }

            runtime = self._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
            startup = yield from self._wait_for_game_startup_page(
                runtime,
                stop_event,
                timeout=startup_timeout,
                poll_seconds=startup_poll,
            )
            if startup.get("ready"):
                break
            self._log(
                "warning",
                "维护复查：游戏启动 5 分钟仍未进入公告/封面/维护页，重新启动模拟器",
            )
            continue

        assert runtime is not None
        # A restart commonly opens #14. Closing it only exposes the cover and
        # must never be treated as proof that service has recovered.
        scene_id = startup.get("scene_id")
        frame = startup.get("frame")
        for _ in range(8):
            self._raise_if_stopped(stop_event)
            if scene_id == 14:
                runtime.click_shape_center(14, "关闭公告")
                yield from runtime.wait_action_settle(2.0)
                scene_id, _score, frame, _text = self._observe_maintenance_scene(runtime, update=True)
                continue
            break

        if scene_id in {415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID}:
            next_time = self._defer_maintenance_recovery(
                scene_id=scene_id,
                evidence={"stage": "post_restart", "scene_id": scene_id, "ocr": runtime.ocr_text(frame)[:160]},
            )
            return {"result": "success", "message": f"维护页 #415 仍在，休眠至 {next_time}"}

        # #18's tiny service label is only a weak hint. Use the user's robust
        # behavioral proof: click Enter every five seconds for half a minute.
        if scene_id == 18:
            for attempt in range(1, probe_attempts + 1):
                self._raise_if_stopped(stop_event)
                runtime.click_shape_center(18, "进入游戏")
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"维护复查：第 {attempt}/{probe_attempts} 次尝试进入游戏",
                        phase="maintenance_probe",
                        current_scene=18,
                    )
                yield from runtime.wait_action_settle(probe_interval)
                scene_id, _score, frame, _text = self._observe_maintenance_scene(runtime, update=True)
                if scene_id in {415, LOGIN_MAINTENANCE_PROMPT_SCENE_ID}:
                    break
                # A click can briefly produce an unrecognized loading frame.
                # Only a formal post-login scene proves that maintenance ended;
                # unknown must keep the global gate closed to ordinary Jobs.
                if self._maintenance_scene_proves_available(scene_id):
                    break

        if not self._maintenance_scene_proves_available(scene_id):
            next_time = self._defer_maintenance_recovery(
                scene_id=scene_id,
                evidence={
                    "stage": "enter_game_probe",
                    "scene_id": scene_id,
                    "attempts": probe_attempts,
                    "duration_seconds": probe_duration,
                    "ocr": runtime.ocr_text(frame)[:160],
                },
            )
            return {
                "result": "success",
                "message": f"半分钟内未观察到正式登录后场景，维护门闩保持开启，休眠至 {next_time}",
            }

        if scene_id != 34:
            # The maintenance recovery and the route back to #34 are separate
            # concerns. This Cell has already proved that the game left the
            # maintenance/cover loop, so preserve that startup fact and hand
            # world recovery to the standard login Job without another MuMu
            # restart.
            mark_mumu_device_startup_ready(reason="maintenance_startup_page_seen")
            clear_maintenance_gate(
                self._maintenance_world_facts_path(),
                evidence={"stage": "login_scheduled", "scene_id": scene_id},
            )
            self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
            self._schedule_login_job_first()
            return {
                "result": "success",
                "message": "维护已结束；已保留启动就绪事实并将“登录”排到队首，不再重启模拟器",
            }

        clear_maintenance_gate(
            self._maintenance_world_facts_path(),
            evidence={"stage": "recovered", "scene_id": 34},
        )
        self._persist_scheduler_task_next_time(MAINTENANCE_RECOVERY_TASK_ID, None)
        runtime.set_completion_message("维护结束，已进入 #34 世界；普通作业恢复调度")
        self._log("success", "维护恢复：已确认进入 #34 世界，解除维护门闩")
        return {"result": "success", "message": "维护结束，已进入 #34 世界"}
