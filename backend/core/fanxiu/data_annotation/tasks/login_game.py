from __future__ import annotations

import threading
import time
from typing import Any


class LoginGameTaskMixin:
    login_game_scene_ids = (14, 15, 16, 17, 18, 19, 20, 21, 22, 34, 47)

    def _execute_login_game_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        """Enter Fanxiu with the current account and stop at the world scene."""
        payload = dict(payload or {})
        runtime = self._fanxiu_runtime(ctx, stop_event=stop_event)
        unknown_timeout = max(10.0, min(120.0, float(payload.get("unknown_timeout_seconds") or 60.0)))
        unknown_started_at: float | None = None

        for _step in range(40):
            self._raise_if_stopped(stop_event)
            scene_id, score, frame = runtime.current_scene(self.login_game_scene_ids, update=True)
            frame_text = runtime.ocr_text(frame)
            if self._world_scene_ocr_confirmed_text(frame_text):
                scene_id = 34
                score = float(self.scene_threshold)
            if scene_id is None:
                strong_scene_id = self._strong_ocr_scene_number(ctx, frame)
                if strong_scene_id in self.login_game_scene_ids:
                    scene_id = int(strong_scene_id)
                    score = float(self.scene_threshold)

            if scene_id == 34:
                runtime.set_completion_message("登录游戏完成，已进入 #34 世界")
                self._log("success", "登录游戏：已进入 #34 世界")
                return "success"

            if scene_id is None:
                overlay = self._known_blocking_overlay_info(ctx)
                if isinstance(overlay, dict) and str(overlay.get("title") or "") == "游戏公告":
                    cleared = yield from self._clear_known_blocking_overlay_if_possible(
                        ctx,
                        stop_event,
                        label="登录游戏",
                        timeout=float(payload.get("announcement_timeout_seconds") or 20.0),
                    )
                    if cleared:
                        unknown_started_at = None
                        continue

                text = frame_text
                compact_text = "".join(str(text or "").split())
                if all(token in compact_text for token in ("进入游戏", "AppVer", "健康游戏忠告")):
                    self._log("info", "登录游戏：全帧 OCR 确认游戏封面，点击当前账号的进入游戏")
                    runtime.click_shape_center(18, "进入游戏")
                    yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                    unknown_started_at = None
                    continue
                if unknown_started_at is None:
                    unknown_started_at = time.monotonic()
                elapsed = time.monotonic() - unknown_started_at
                if elapsed >= unknown_timeout:
                    raise RuntimeError(
                        f"登录游戏：{unknown_timeout:.0f}s 内未识别到登录链场景，OCR={text[:160] or '空'}"
                    )
                with self._lock:
                    self._set_status_locked(
                        "running",
                        f"登录游戏：等待游戏加载，已等待 {elapsed:.0f}s",
                        phase="login_game_wait_loading",
                    )
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue

            unknown_started_at = None
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"登录游戏：当前 #{scene_id} {score:.0f}%",
                    phase="login_game",
                    current_scene=scene_id,
                )

            if scene_id == 14:
                yield from runtime.wait_click_then_view(
                    14,
                    "关闭公告",
                    18,
                    timeout=float(payload.get("announcement_timeout_seconds") or 20.0),
                    max_clicks=1,
                    label="登录游戏：关闭公告后等待 #18 游戏封面",
                )
                continue
            if scene_id == 15:
                yield from runtime.wait_click_then_view(
                    15,
                    "登录",
                    [17, 18],
                    timeout=float(payload.get("login_timeout_seconds") or 30.0),
                    label="登录游戏：使用当前账号登录",
                )
                continue
            if scene_id == 16:
                raise RuntimeError("登录游戏：进入 #16 挑选账号；为避免误登，请人工选择账号后重新运行")
            if scene_id == 17:
                yield from runtime.wait_click_then_view(
                    17,
                    "同意",
                    18,
                    timeout=float(payload.get("agreement_timeout_seconds") or 30.0),
                    label="登录游戏：同意服务协议后等待 #18 游戏封面",
                )
                continue
            if scene_id == 18:
                runtime.click_shape_center(18, "进入游戏")
                yield from runtime.wait_action_settle(float(payload.get("loading_poll_seconds") or 2.0))
                continue
            if scene_id == 19:
                yield from runtime.wait_click_then_view(
                    19,
                    "空白",
                    [19, 20, 21, 22, 34, 47],
                    timeout=float(payload.get("popup_timeout_seconds") or 20.0),
                    max_clicks=1,
                    label="登录游戏：关闭修炼弹窗",
                )
                continue
            if scene_id in {21, 22}:
                yield from runtime.wait_click_then_view(
                    scene_id,
                    "关闭",
                    [19, 20, 21, 22, 34, 47],
                    timeout=float(payload.get("popup_timeout_seconds") or 20.0),
                    max_clicks=1,
                    label=f"登录游戏：关闭 #{scene_id} 登录弹窗",
                )
                continue
            if scene_id in {20, 47}:
                yield from runtime.goto_view(34)
                continue

            raise RuntimeError(f"登录游戏：暂不支持从 #{scene_id} 继续")

        raise RuntimeError("登录游戏：达到最大步骤数，仍未进入 #34 世界")
