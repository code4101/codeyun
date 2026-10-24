from __future__ import annotations

import time
from datetime import datetime, timedelta
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
# A real accepted entry on 2026-10-24 was still loading after the old
# 30-second observation, then naturally reached #338 without another click.
# World can be a transient transfer landing; keep observing the SAME request.
# This budget never authorizes another entry or treats missing counters as zero.
_MOZU_LANDING_OBSERVE_SECONDS = 180.0
_MOZU_BATTLEFIELD_SCENES = frozenset({338, 557})


def mozu_snapshot_facts(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Diagnostic projection; counters are only trusted from complete snapshots."""
    snapshot = dict(snapshot or {})
    complete = snapshot.get("complete") is True
    left_times = snapshot.get("left_times") if complete else None
    return {
        "complete": complete,
        "reason": snapshot.get("reason"),
        "left_times": left_times if isinstance(left_times, int) else None,
    }


def _landing_scene_id(view: Any) -> int | None:
    scene_id = getattr(view, "id", view)
    return scene_id if isinstance(scene_id, int) else None


def judge_mozu_landing(
    scene_id: int | None,
    before_snapshot: dict[str, Any] | None,
    after_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pure participation decision after the single entry click.

    Participation is proven only by an explicit battlefield scene id or a
    decrease between two complete Runtime counters. Generic #85, the world,
    unknown scenes or missing counters never prove participation.
    """
    before = mozu_snapshot_facts(before_snapshot)
    after = mozu_snapshot_facts(after_snapshot)
    runtime_confirmed = (
        before["left_times"] is not None
        and after["left_times"] is not None
        and after["left_times"] < before["left_times"]
    )
    entry_observed = scene_id in _MOZU_BATTLEFIELD_SCENES
    return {
        "scene_id": scene_id,
        "entry_observed": entry_observed,
        "runtime_confirmed": runtime_confirmed,
        "participation_confirmed": entry_observed or runtime_confirmed,
        "before": before,
        "after": after,
    }


def mozu_post_participation_exit(scene_id: int | None) -> str | None:
    """Pure exit decision after the minimum participation wait.

    Applies to every participation_confirmed branch, including a counter-only
    confirmation. Only a dedicated battlefield id may be left via its own
    "离开" shape; the world/#339 are already safe. Generic #85 or unknown
    scenes return None: keep the scene and fail, never leave blindly.
    """
    if scene_id in _MOZU_BATTLEFIELD_SCENES:
        return "leave"
    if scene_id in {20, 34, 339}:
        return "safe"
    return None


def plan_daily_mozu_admission(now: datetime) -> dict[str, Any] | None:
    """Pure entry-window decision; a normal skip never proves participation.

    The technical execution can succeed while no game action took place.
    Existing entry/exit evidence belongs to its original attempt and must not
    be inferred from this later scheduling decision.
    """
    window_start = now.replace(hour=12, minute=30, second=0, microsecond=0)
    window_end = now.replace(hour=12, minute=35, second=0, microsecond=0)
    if window_start <= now <= window_end:
        return None
    next_run = window_start if now < window_start else window_start + timedelta(days=1)
    return {
        "result": "success", "outcome": "skipped_window",
        "game_actions_performed": False,
        "message": "日常_魔祖：当前不在 12:30:00-12:35:00 窗口，未执行游戏操作",
        "next_time": next_run.strftime("%Y-%m-%d %H:%M:%S"),
        "current_scene": None,
    }


class MozuTaskMixin:
    def daily_mozu_admission(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = dict(payload or {})
        if payload.get("finish_only") is True:
            return None
        return self._persist_admission_decision(payload, plan_daily_mozu_admission(job_now()))

    def daily_mozu_finish_flow(self, context: Any):
        """仅收尾已进入的魔祖战场；允许窗口外执行，绝不重新参战。

        用于已确认参战而退出失败的正式 Job 人工复验。
        世界页只证明收尾已完成，允许退出成功后写回失败的幂等复验，
        不以此推断参与或奖励成功。
        """
        view = yield from context.wait_scene([338, 557, 34], wait=30.0)
        scene_id = view.scene_id
        if scene_id not in {338, 557, 34}:
            raise RuntimeError("日常_魔祖：仅收尾要求已确认的魔祖战场")
        if scene_id != 34:
            yield from context.wait_click_then_scene(
                scene_id, "离开", [186, 339, 34], timeout=60.0, settle_seconds=1.5
            )
            yield from context.go_scene(34)
        landed = yield from context.wait_scene([34], wait=15.0)
        if landed.scene_id != 34:
            raise RuntimeError("日常_魔祖：收尾未确认返回世界")
        next_run = (job_now() + timedelta(days=1)).replace(hour=12, minute=30, second=0, microsecond=0)
        context.set_next_time(next_run.strftime("%Y-%m-%d %H:%M:%S"))
        return {"result": "success", "message": "日常_魔祖：已确认战场安全退出并返回世界（仅收尾）", "exit_confirmed": True, "current_scene": 34}

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
        # 战场传送会经过 #661/#85；两次真实故障均在通用 12 秒预算
        # 耗尽后才到达 #338。等待完整加载，不重发已接受的参战请求。
        completed_view = yield from context.wait_click_then_scene(
            337, "前往", [338, 34, 339], timeout=60.0,
            retry_if_source_remains=False,
        )
        # 入口只点一次。通用 #85、瞬态世界或未知落点都不能证明参战：在有界
        # 时间内显式观察战场 scene_id 或完整计数下降，否则保留现场失败。
        judgement = judge_mozu_landing(
            _landing_scene_id(completed_view), before_snapshot, read_demon_boss_snapshot()
        )
        deadline = time.monotonic() + _MOZU_LANDING_OBSERVE_SECONDS
        while not judgement["participation_confirmed"] and time.monotonic() < deadline:
            yield from context.wait_action_settle(1.0)
            observed = yield from context.wait_scene(
                [338, 557, 339, 34], wait=min(5.0, max(0.0, deadline - time.monotonic())),
                required=False, label="日常_魔祖：有界观察入口落点",
            )
            judgement = judge_mozu_landing(
                _landing_scene_id(observed), before_snapshot, read_demon_boss_snapshot()
            )
        if not judgement["participation_confirmed"]:
            raise RuntimeError(
                "日常_魔祖：入口请求后 "
                f"{_MOZU_LANDING_OBSERVE_SECONDS:.0f} 秒内未确认魔祖战场或完整计数下降，保留现场；"
                f"当前 #{judgement['scene_id'] or 'unknown'}，"
                f"before={judgement['before']}，after={judgement['after']}"
            )
        completed_scene_id = judgement["scene_id"]
        entry_observed = judgement["entry_observed"]
        runtime_confirmed = judgement["runtime_confirmed"]
        before_left_times = judgement["before"]["left_times"]
        after_left_times = judgement["after"]["left_times"]

        # 计数下降只证明已参战，不证明最低驻留已完成：所有确认分支统一等待。
        yield from context.wait_action_settle(_MOZU_PARTICIPATION_SECONDS)
        # 活动战场本身可能持续二十多分钟；完成最低参战时间后使用已有
        # 安全离开图标主动退出，不能把“等整场结束”当收尾。通用 #85 或
        # 未知落点不得凭此盲目离场，保留现场失败。
        _wait_scene_match = yield from context.wait_scene([338, 557, 20, 34, 339], wait=5.0, required=False)
        scene_id = _wait_scene_match.scene_id if _wait_scene_match is not None else None
        exit_action = mozu_post_participation_exit(scene_id)
        if exit_action == "leave":
            transition = yield from context.wait_click_then_scene(
                scene_id,
                "离开",
                [186, 339, 34],
                timeout=25.0,
                settle_seconds=1.5,
                label="日常_魔祖：最低参战时间完成后主动离开战场",
            )
            completed_scene_id = getattr(transition, "id", transition)
        elif exit_action == "safe":
            completed_scene_id = scene_id
        else:
            raise RuntimeError(
                "日常_魔祖：已确认参战并等待最低参战时间，但未识别到专属战场或安全世界，"
                f"保留现场；当前 #{scene_id or 'unknown'}"
            )
        yield from context.go_scene(34)
        # Navigation returns an execution status, not a scene identity. Use
        # the same real-scene verification as an idempotent finish-only replay.
        finished = yield from self.daily_mozu_finish_flow(context)
        completed_scene_id = finished["current_scene"]
        exit_confirmed = finished["exit_confirmed"]
        final_facts = mozu_snapshot_facts(read_demon_boss_snapshot())
        context.set_next_time(next_run_text)
        evidence = (
            f"Runtime 剩余次数 {before_left_times}->{after_left_times}"
            if runtime_confirmed
            else "已进入魔祖战场"
        )
        return {
            "result": "success",
            "message": (
                f"日常_魔祖：{evidence}，"
                + f"已参战并运行至少 30 秒，离开后返回世界 #{completed_scene_id}"
            ),
            "current_scene": completed_scene_id,
            "runtime_confirmed": runtime_confirmed,
            "entry_observed": entry_observed,
            "exit_confirmed": exit_confirmed,
            "left_times": final_facts["left_times"],
            "snapshots": {"before": judgement["before"], "after": judgement["after"], "final": final_facts},
        }
