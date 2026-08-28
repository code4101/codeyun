from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values


def _now() -> datetime:
    return datetime.now()


def next_daily_xuanhuang_time(now: datetime | None = None) -> str:
    """Return the next calendar day's 05:00 trigger."""

    current = now or _now()
    return (current + timedelta(days=1)).replace(
        hour=5,
        minute=0,
        second=0,
        microsecond=0,
    ).strftime("%Y-%m-%d %H:%M:%S")


class DailyXuanhuangTaskMixin:
    def _daily_xuanhuang_runtime_snapshot(
        self,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        override = (payload or {}).get(
            "__daily_xuanhuang_runtime_snapshot_override"
        )
        if isinstance(override, dict):
            return dict(override)
        try:
            from backend.core.fanxiu.instrumentation.xuanhuang import (
                read_xuanhuang_snapshot,
            )

            return read_xuanhuang_snapshot()
        except Exception as exc:
            return {
                "ok": False,
                "available": False,
                "complete": False,
                "source": "runtime_memory",
                "reason": f"{type(exc).__name__}: {exc}",
            }

    def _daily_xuanhuang_open_counter(
        self,
        runtime: Any,
        *,
        payload: dict[str, Any] | None,
        view_timeout: float,
        recommend_timeout_seconds: float,
    ):
        yield from runtime.goto_view(69)
        entry_result = yield from runtime.open_daily_entry(
            label="日常_玄荒",
            title_pattern="玄荒",
            progress_can_mark_done=False,
            zero_progress_can_mark_done=False,
            max_scrolls=30,
            initial_checks=3,
        )
        if entry_result == "done":
            raise RuntimeError(
                "日常_玄荒：日常列表状态不能作为完成证据，必须进入 #418 读取次数"
            )
        if entry_result != "open":
            raise RuntimeError(f"日常_玄荒：无法打开日常入口，结果={entry_result!r}")

        yield from runtime.wait_view(
            400,
            timeout=view_timeout,
            label="日常_玄荒：等待玄荒入口 #400",
        )
        yield from runtime.wait_click(
            400,
            "玄荒",
            timeout=view_timeout,
        )
        yield from runtime.wait_view(
            417,
            timeout=view_timeout,
            label="日常_玄荒：等待推荐窗口 #417",
        )

        # 进入玄荒模块后计数管理器已经加载。若动态事实明确为 0，
        # 不应再依赖推荐列表 OCR 才能进入 #418；已完成时推荐项本身
        # 可能消失，继续找“推”只会制造无意义重试。
        runtime_snapshot = self._daily_xuanhuang_runtime_snapshot(payload)
        runtime_remaining = runtime_snapshot.get("remaining")
        if (
            runtime_snapshot.get("complete") is True
            and runtime_snapshot.get("counter_loaded") is True
            and isinstance(runtime_remaining, int)
            and not isinstance(runtime_remaining, bool)
            and runtime_remaining == 0
        ):
            log = getattr(self, "_log", None)
            if callable(log):
                log(
                    "detail",
                    "日常_玄荒：#417 Runtime 读取剩余次数 0，"
                    "跳过推荐列表 OCR（GodsoultowerData.leftChangeTimes）",
                )
            return "done"

        match = yield from runtime.wait_ocr_any_text(
            417,
            ("推", "荐"),
            in_shapes=["窗口"],
            padding=0,
            timeout_seconds=recommend_timeout_seconds,
        )
        if match is None:
            # #417 默认停在 3 级。角色战力不满足 3 级推荐条件时，
            # 当前列表即使完整左右遍历也不会出现“推”；切到 2 级后
            # 必须重新从头遍历同一个可加载窗口，再决定是否失败。
            yield from runtime.wait_click(
                417,
                "2级",
                timeout=view_timeout,
            )
            match = yield from runtime.wait_ocr_any_text(
                417,
                ("推", "荐"),
                in_shapes=["窗口"],
                padding=0,
                timeout_seconds=recommend_timeout_seconds * 3,
                direction_cycles=3,
                cycle_pause_seconds=2,
            )
        if match is None:
            raise TimeoutError(
                f"日常_玄荒：3级完整滚动 {recommend_timeout_seconds:g} 秒，"
                f"2级三轮往返滚动 {recommend_timeout_seconds * 3:g} 秒后"
                "仍未识别到「推」或「荐」"
            )
        runtime.click_frame_point(
            417,
            float(match.x) - float(match.w),
            float(match.y) + 2 * float(match.h),
        )

        yield from runtime.wait_view(
            418,
            timeout=view_timeout,
            label="日常_玄荒：等待挑战页 #418",
        )
        return "open"

    def _daily_xuanhuang_read_remaining(
        self,
        runtime: Any,
        *,
        payload: dict[str, Any] | None = None,
        attempts: int,
        retry_seconds: float,
    ):
        runtime_snapshot = self._daily_xuanhuang_runtime_snapshot(payload)
        runtime_remaining = runtime_snapshot.get("remaining")
        if (
            runtime_snapshot.get("complete") is True
            and runtime_snapshot.get("counter_loaded") is True
            and isinstance(runtime_remaining, int)
            and not isinstance(runtime_remaining, bool)
            and runtime_remaining >= 0
        ):
            log = getattr(self, "_log", None)
            if callable(log):
                log(
                    "detail",
                    "日常_玄荒：Runtime 读取剩余次数 "
                    f"{runtime_remaining}"
                    "（GodsoultowerData.leftChangeTimes）",
                )
            return runtime_remaining

        last_text = ""
        for attempt in range(attempts):
            frame = runtime.cur_frame(update=True)
            _numbers, last_text = runtime.ocr_numbers_in_shapes(
                418,
                ("次数",),
                padding=16,
                frame_data_url=frame,
            )
            fraction = parse_ocr_values(last_text, expected_count=2)
            if fraction is not None:
                remaining, total = fraction
                if 0 <= remaining <= total:
                    return remaining
            if attempt + 1 < attempts:
                yield from runtime.wait_action_settle(retry_seconds)
        raise RuntimeError(
            f"日常_玄荒：无法从 #418[次数] 稳定识别分子/分母，OCR={last_text!r}"
        )

    def _daily_xuanhuang_wait_battle_done(
        self,
        runtime: Any,
        *,
        timeout_seconds: float,
        poll_seconds: float,
    ):
        started_at = time.monotonic()
        # ``timeout_seconds`` is a loss-of-liveness deadline, not a total
        # battle-duration limit.  Real Xuanhuang fights can stay healthy for
        # well over 30 minutes; cutting them off by wall clock releases the
        # device to unrelated Scheduler jobs while the game is still in
        # battle.  Every positively recognized battle frame renews the lease.
        deadline = started_at + timeout_seconds
        next_status_at = started_at
        saw_battle_scene = False
        while True:
            frame = runtime.cur_frame(update=True)
            scene_id, _score, _frame = runtime.current_scene(
                [186, 419, 420],
                frame_data_url=frame,
            )
            if scene_id == 420:
                return saw_battle_scene
            if scene_id in {186, 419}:
                saw_battle_scene = True
                deadline = time.monotonic() + timeout_seconds
            now = time.monotonic()
            if now >= next_status_at:
                status_setter = getattr(self, "_set_status_locked", None)
                status_lock = getattr(self, "_lock", None)
                if callable(status_setter) and status_lock is not None:
                    with status_lock:
                        status_setter(
                            "running",
                            f"日常_玄荒：自动战斗中，已等待 {int(now - started_at)} 秒",
                            phase="daily_xuanhuang_battle",
                            current_scene=scene_id,
                        )
                next_status_at = now + 30.0
            if now >= deadline:
                final_frame = runtime.cur_frame(update=True)
                final_scene, _score, _frame = runtime.current_scene(
                    [186, 419, 420],
                    frame_data_url=final_frame,
                )
                if final_scene == 420:
                    return saw_battle_scene
                if final_scene in {186, 419}:
                    saw_battle_scene = True
                    deadline = time.monotonic() + timeout_seconds
                    yield from runtime.wait_action_settle(poll_seconds)
                    continue
                raise TimeoutError(
                    "日常_玄荒：连续无法识别战斗 #186/#419 或结算 #420 "
                    f"超过 {timeout_seconds:g} 秒"
                )
            yield from runtime.wait_action_settle(poll_seconds)

    def _daily_xuanhuang_leave_finished_battle(
        self,
        runtime: Any,
        *,
        view_timeout: float,
    ):
        yield from runtime.wait_click(
            420,
            "离开",
            timeout=view_timeout,
        )
        landing = yield from runtime.wait_view(
            34,
            395,
            85,
            timeout=max(30.0, view_timeout),
            label="日常_玄荒：离开战斗后等待世界 #34、副本 #395 或区域内页 #85",
        )
        if landing.id == 85:
            # Real #420 departure can land on the formally recognized
            # generic region interior. Use its own annotated Leave control;
            # do not wait for #34/#395 until the already-known #85 is gone.
            yield from runtime.wait_click(
                85,
                "离开",
                timeout=view_timeout,
            )
            region_landing = yield from runtime.wait_view(
                34,
                86,
                55,
                timeout=max(30.0, view_timeout),
                label="日常_玄荒：#85 离开后等待世界、确认或大地图",
            )
            if region_landing.id == 86:
                yield from runtime.wait_click(86, "确认", timeout=view_timeout)
                yield from runtime.wait_view(
                    34,
                    timeout=max(30.0, view_timeout),
                    label="日常_玄荒：确认离开区域后等待世界 #34",
                )
            elif region_landing.id == 55:
                yield from runtime.goto_view(34)
        if landing.id == 395:
            # #395 与 #420 使用同一右侧「离开」按钮位置。复用既有
            # #420 shape 坐标，不修改资产树；该点击会进入通用 #86
            # 确认弹窗，然后才能真正返回世界。
            runtime.click_shape_center(420, "离开")
            confirm = yield from runtime.wait_view(
                34,
                86,
                55,
                timeout=max(30.0, view_timeout),
                label="日常_玄荒：副本 #395 离开后等待确认、世界或大地图",
            )
            if confirm.id == 86:
                yield from runtime.wait_click(
                    86,
                    "确认",
                    timeout=view_timeout,
                )
                yield from runtime.wait_view(
                    34,
                    timeout=max(30.0, view_timeout),
                    label="日常_玄荒：确认离开副本后等待世界 #34",
                )
            elif confirm.id == 55:
                # 实机存在直接落到 #55「大地图」的分支；通用场景图
                # 已能从 #55 安全返回 #34，复用它而不新增/修改标注。
                yield from runtime.goto_view(34)

    def _run_daily_xuanhuang_flow(
        self,
        runtime: Any,
        payload: dict[str, Any],
    ):
        view_timeout = max(1.0, float(payload.get("view_timeout") or 60.0))
        recommend_timeout_seconds = max(
            0.01,
            float(payload.get("recommend_timeout_seconds") or 60.0),
        )
        battle_timeout_seconds = max(
            0.01,
            # This is the maximum continuous time without recognizing either
            # an active battle or its result. Positively recognized battle
            # frames renew the lease; the outer Job budget remains the hard
            # ceiling for a genuinely endless fight.
            float(payload.get("battle_timeout_seconds") or 120.0),
        )
        battle_poll_seconds = max(
            0.0,
            float(payload.get("battle_poll_seconds") or 1.0),
        )
        battle_entry_timeout_seconds = max(
            1.0,
            float(payload.get("battle_entry_timeout_seconds") or 15.0),
        )
        battle_entry_max_clicks = max(
            1,
            int(payload.get("battle_entry_max_clicks") or 3),
        )
        counter_attempts = max(1, int(payload.get("counter_attempts") or 8))
        counter_retry_seconds = max(
            0.0,
            float(payload.get("counter_retry_seconds") or 0.8),
        )
        current_scene_probe_attempts_value = payload.get(
            "current_scene_probe_attempts",
            payload.get("resume_probe_attempts") or 3,
        )
        current_scene_probe_attempts = max(1, int(current_scene_probe_attempts_value))
        current_scene_probe_retry_seconds_value = payload.get(
            "current_scene_probe_retry_seconds",
            payload.get("resume_probe_retry_seconds") or 0.5,
        )
        current_scene_probe_retry_seconds = max(0.0, float(current_scene_probe_retry_seconds_value))
        resume_transition_timeout_seconds = max(
            1.0,
            float(payload.get("resume_transition_timeout_seconds") or 60.0),
        )
        max_rounds = max(1, int(payload.get("max_rounds") or 20))
        rounds_completed = 0

        start_from_counter = False
        resume_battle_scene: int | None = None
        for attempt in range(current_scene_probe_attempts):
            # 作业入口必须先回答“当前实际在哪”，不能只把 #418 当作一次性
            # 候选探针；后者偶发漏判时会错误地先 goto #34，丢掉已在挑战页
            # 且次数为 0 的业务终态。
            current_scene, _score, _frame = runtime.current_scene(update=True)
            if current_scene == 418:
                start_from_counter = True
                break
            if current_scene in {186, 419, 420}:
                resume_battle_scene = current_scene
                break
            if current_scene is not None:
                break
            if attempt + 1 < current_scene_probe_attempts:
                yield from runtime.wait_action_settle(current_scene_probe_retry_seconds)

        if current_scene is None:
            # A long #186/#419 battle can finish between Scheduler attempts.
            # Its departure briefly renders a blank transition frame with no
            # actionable control. Treating that frame as a missing route makes
            # goto_view(34) fail before the world page has time to appear.
            # Wait only for existing safe anchors; this adds no asset identity
            # and performs no irreversible click during the transition.
            resumed = yield from runtime.wait_view(
                34,
                418,
                186,
                419,
                420,
                85,
                395,
                86,
                55,
                timeout=resume_transition_timeout_seconds,
                label="日常_玄荒：等待战斗退出过渡落到已知页面",
            )
            current_scene = resumed.id
            if current_scene == 418:
                start_from_counter = True
            elif current_scene in {186, 419, 420}:
                resume_battle_scene = current_scene
            elif current_scene in {85, 395, 86, 55}:
                yield from runtime.goto_view(34)

        while True:
            if resume_battle_scene is not None:
                if resume_battle_scene != 420:
                    yield from self._daily_xuanhuang_wait_battle_done(
                        runtime,
                        timeout_seconds=battle_timeout_seconds,
                        poll_seconds=battle_poll_seconds,
                    )
                yield from self._daily_xuanhuang_leave_finished_battle(
                    runtime,
                    view_timeout=view_timeout,
                )
                rounds_completed += 1
                resume_battle_scene = None
                continue
            if not start_from_counter:
                yield from runtime.goto_view(34)
                counter_state = yield from self._daily_xuanhuang_open_counter(
                    runtime,
                    payload=payload,
                    view_timeout=view_timeout,
                    recommend_timeout_seconds=recommend_timeout_seconds,
                )
                if counter_state == "done":
                    yield from runtime.goto_view(34)
                    runtime.set_next_time(next_daily_xuanhuang_time())
                    return {
                        "result": "success",
                        "message": (
                            "日常_玄荒：Runtime 次数已为 0，今日完成"
                            f"（本次挑战 {rounds_completed} 轮）"
                        ),
                        "rounds_completed": rounds_completed,
                        "current_scene": 34,
                    }
            start_from_counter = False
            remaining = yield from self._daily_xuanhuang_read_remaining(
                runtime,
                payload=payload,
                attempts=counter_attempts,
                retry_seconds=counter_retry_seconds,
            )
            if remaining == 0:
                yield from runtime.goto_view(34)
                runtime.set_next_time(next_daily_xuanhuang_time())
                return {
                    "result": "success",
                    "message": (
                        f"日常_玄荒：次数分子已为 0，今日完成"
                        f"（本次挑战 {rounds_completed} 轮）"
                    ),
                    "rounds_completed": rounds_completed,
                    "current_scene": 34,
                }
            if rounds_completed >= max_rounds:
                raise RuntimeError(
                    f"日常_玄荒：已挑战 {rounds_completed} 轮但剩余次数仍为 {remaining}"
                )

            # #418 的「前往」偶发会吞掉一次点击。不能在没有确认页面状态的
            # 情况下直接补点（挑战次数属于不可逆动作）；复用 Runtime 的
            # wait_click_then_view：只有新帧仍可靠识别为源场景 #418 时才重试，
            # 一旦已进入 #419/#420 或变成 unknown 就停止补点。
            yield from runtime.wait_click_then_view(
                418,
                "前往",
                186,
                419,
                420,
                timeout=battle_entry_timeout_seconds,
                settle_seconds=1.0,
                retry_if_source_remains=True,
                max_clicks=battle_entry_max_clicks,
                label="日常_玄荒：点击前往后等待战斗 #186/#419 或结算 #420",
            )
            yield from self._daily_xuanhuang_wait_battle_done(
                runtime,
                timeout_seconds=battle_timeout_seconds,
                poll_seconds=battle_poll_seconds,
            )
            yield from self._daily_xuanhuang_leave_finished_battle(
                runtime,
                view_timeout=view_timeout,
            )
            rounds_completed += 1

    def _execute_daily_xuanhuang_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        payload = dict(payload or {})
        asset_tree_path = ctx.get("asset_tree_path")
        if not isinstance(asset_tree_path, Path):
            raise RuntimeError("缺少日常_玄荒资产树路径，无法执行作业")
        return (
            yield from self._execute_daily_runtime_task(
                ctx,
                stop_event,
                payload,
                task_type="daily_xuanhuang",
                label="日常_玄荒",
                flow=lambda runtime: self._run_daily_xuanhuang_flow(runtime, payload),
            )
        )
