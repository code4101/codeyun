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

    def daily_mozu_flow(self, context: Any):
        now = job_now()
        window_start = now.replace(hour=12, minute=30, second=0, microsecond=0)
        next_run_text = (window_start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        yield from context.go_scene(34)
        yield from context.wait_click_then_scene(34, "日程", 66)
        yield from context.wait_action_settle(3.0)
        try:
            yield from select_schedule_activity(context, r"魔祖", enter=True)
        except ScheduleActivityNotFoundError as exc:
            if not exc.exhaustive:
                raise
            context.set_next_time(next_run_text)
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
        yield from context.wait_scene(
            [336], wait=20.0, label="日常_魔祖：等待已校验的活动卡片进入 #336"
        )
        yield from context.wait_click_then_scene(336, "前往", 337)
        before_snapshot = read_demon_boss_snapshot()
        completed_view = yield from context.wait_click_then_scene(337, "前往", [338, 34, 339])
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
                delayed = yield from context.wait_scene(
                    [338,
                    339],
                    wait=20.0,
                    label="日常_魔祖：等待延迟战场落点",
                )
                completed_scene_id = getattr(delayed, "id", delayed)
                entry_observed = completed_scene_id == 338
            except TimeoutError:
                completed_scene_id = 34

        if entry_observed:
            yield from context.wait_action_settle(_MOZU_PARTICIPATION_SECONDS)
            # 活动战场本身可能持续二十多分钟；完成最低参战时间后使用已有
            # 安全离开图标和通用确认框主动退出，不能把“等整场结束”当收尾。
            scene_id, _score, _frame = (yield from context.current_scene([338, 557, 20, 34, 339], update=True))
            if scene_id in {338, 557}:
                transition = yield from context.wait_click_then_scene(
                    scene_id,
                    "离开",
                    [186, 339, 34],
                    timeout=25.0,
                    settle_seconds=1.5,
                    label="日常_魔祖：最低参战时间完成后主动离开战场",
                )
                completed_scene_id = getattr(transition, "id", transition)
            elif scene_id in {20, 34, 339}:
                completed_scene_id = scene_id
            else:
                raise RuntimeError(f"日常_魔祖：最低参战时间后未识别到可退出战场，当前 #{scene_id or 'unknown'}")
        landed = yield from context.go_scene(34)
        completed_scene_id = getattr(landed, "id", landed)
        exit_confirmed = completed_scene_id == 34
        final_snapshot = read_demon_boss_snapshot()
        final_left_times = (
            final_snapshot.get("left_times")
            if final_snapshot.get("complete")
            else after_left_times
        )
        context.set_next_time(next_run_text)
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
