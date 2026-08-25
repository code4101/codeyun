from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.schedule_navigation import (
    ScheduleActivityNotFoundError,
    select_schedule_activity,
)
from backend.core.fanxiu.instrumentation.demon_boss import (
    read_demon_boss_snapshot,
)


_MOZU_PARTICIPATION_SECONDS = 30.0


class MozuTaskMixin:
    def daily_mozu_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        now = job_now()
        window_start = now.replace(hour=12, minute=30, second=0, microsecond=0)
        window_end = now.replace(hour=12, minute=35, second=0, microsecond=0)
        next_run = window_start if now < window_start else window_start + timedelta(days=1)
        next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
        if window_start <= now <= window_end:
            return None
        return self._persist_admission_decision(payload, {
            "result": "success",
            "message": "日常_魔祖：当前不在 12:30:00-12:35:00 窗口，未执行游戏操作",
            "next_time": next_run_text,
            "current_scene": None,
        })

    def daily_mozu_flow(self, runtime: Any):
        now = job_now()
        window_start = now.replace(hour=12, minute=30, second=0, microsecond=0)
        next_run_text = (window_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        yield from runtime.go_scene(34)
        yield from runtime.wait_click_then_view(34, "日程", 66)
        yield from runtime.wait_action_settle(3.0)
        try:
            yield from select_schedule_activity(runtime, r"魔祖", enter=True)
        except ScheduleActivityNotFoundError as exc:
            if not exc.exhaustive:
                raise
            runtime.set_next_time(next_run_text)
            return {
                "result": "success",
                "message": (
                    "日常_魔祖：已逐页核对日程活动卡，今日没有覆盖当前时点的魔祖活动，"
                    f"未执行游戏操作；下次 {next_run_text}"
                ),
                "current_scene": 66,
                "runtime_confirmed": False,
                "entry_observed": False,
                "exit_confirmed": False,
                "left_times": None,
            }
        yield from runtime.wait_view(
            336, timeout=20.0, label="日常_魔祖：等待已校验的活动卡片进入 #336"
        )
        yield from runtime.wait_click_then_view(336, "前往", 337)
        before_snapshot = read_demon_boss_snapshot()
        completed_view = yield from runtime.wait_click_then_view(337, "前往", [338, 34, 339])
        completed_scene_id = getattr(completed_view, "id", completed_view)
        after_entry_snapshot = read_demon_boss_snapshot()
        before_left_times = (
            before_snapshot.get("left_times")
            if before_snapshot.get("complete")
            else None
        )
        after_left_times = (
            after_entry_snapshot.get("left_times")
            if after_entry_snapshot.get("complete")
            else None
        )
        runtime_confirmed = (
            isinstance(before_left_times, int)
            and isinstance(after_left_times, int)
            and after_left_times < before_left_times
        )
        entry_observed = completed_scene_id == 338
        if completed_scene_id == 34 and not runtime_confirmed:
            # The entry request can transiently render the world before the
            # delayed battlefield transport. Do not turn that early #34 into
            # a false participation result.
            try:
                delayed = yield from runtime.wait_view(
                    338,
                    339,
                    timeout=20.0,
                    label="日常_魔祖：等待延迟战场落点",
                )
                completed_scene_id = getattr(delayed, "id", delayed)
                entry_observed = completed_scene_id == 338
            except TimeoutError:
                completed_scene_id = 34

        if entry_observed:
            yield from runtime.wait_action_settle(_MOZU_PARTICIPATION_SECONDS)
        landed = yield from runtime.goto_view(34)
        completed_scene_id = getattr(landed, "id", landed)
        exit_confirmed = completed_scene_id == 34
        final_snapshot = read_demon_boss_snapshot()
        final_left_times = (
            final_snapshot.get("left_times")
            if final_snapshot.get("complete")
            else after_left_times
        )
        runtime.set_next_time(next_run_text)
        evidence = (
            f"Runtime 剩余次数 {before_left_times}->{after_left_times}"
            if runtime_confirmed
            else (
                "已进入魔祖战场"
                if entry_observed
                else "入口请求已返回，但未观察到战场"
            )
        )
        return {
            "result": "success",
            "message": (
                f"日常_魔祖：{evidence}，"
                + (
                    f"已参战并运行至少 30 秒，离开后返回世界 #{completed_scene_id}"
                    if entry_observed
                    else f"未重复进入，当前 #{completed_scene_id}"
                )
            ),
            "current_scene": completed_scene_id,
            "runtime_confirmed": runtime_confirmed,
            "entry_observed": entry_observed,
            "exit_confirmed": exit_confirmed,
            "left_times": final_left_times,
        }
