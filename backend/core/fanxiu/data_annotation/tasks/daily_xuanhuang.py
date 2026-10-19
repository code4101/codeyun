from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.core.fanxiu.data_annotation.ocr_values import parse_ocr_values


# Live Xuanhuang uses both dedicated and shared battle layouts. None of
# these scenes proves settlement; only #420 ends the battle wait.
XUANHUANG_BATTLE_SCENES = frozenset({85, 186, 419, 678})
XUANHUANG_DAILY_TASK_ID = 1049


def xuanhuang_daily_target_complete(snapshot: dict[str, Any]) -> bool:
    """只接受当前 Quest 日常任务的完整事实；缺失不能当作已完成。

    ActiveTask 1049 的 GodSoulTowerKillNum 是击杀参与目标；服务器的
    targetTurn 可以覆盖静态 timesLimit，不能写死每天 1 次或 3 次。
    finishTasks 是原生已完成集合，完成后任务条目可被移除。
    """
    if (snapshot.get("complete") is not True
            or snapshot.get("task_id") != XUANHUANG_DAILY_TASK_ID
            or not (snapshot.get("present") is True or snapshot.get("done") is True)):
        raise RuntimeError("日常_玄荒：日常任务1049事实未完整加载，不能判断参与目标")
    if snapshot.get("done") is not True:
        turn, target = snapshot.get("turn"), snapshot.get("target_turn")
        if (type(turn) is not int or type(target) is not int
                or turn < 0 or target <= 0 or turn > target):
            raise RuntimeError("日常_玄荒：日常任务1049参与进度不完整或矛盾")
    return snapshot.get("done") is True


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


def leave_xuanhuang(context: Any, *, view_timeout: float = 60.0):
    """执行已获准的玄荒退出；离开确认统一由弹窗守护处理。

    点击离开后只等待最终落点，不把弹窗出现前短暂的 #85 当作下一步。
    未到最终落点就停止；本函数不决定何时结束战斗。
    """
    timeout = max(30.0, float(view_timeout))
    landing = yield from context.wait_scene(
        [420, 395, 85, 34, 55], wait=timeout, label="日常_玄荒：识别退出起点",
    )
    # 2026-10-19 实机：#476 确认后整段等待仍停在同一副本 #85（离开请求被吞）。
    # 离开已清场副本无资源副作用；只有完整等待仍在副本内才重走一次离开。
    for leave_round in range(2):
        if landing.id in (420, 85):
            yield from context.wait_click(landing.id, "离开", timeout=timeout)
        elif landing.id == 395:
            # #395 复用现有 #420 的同位置离开标注。
            context.click_shape_center(420, "离开")
        elif landing.id not in (34, 55):
            raise RuntimeError(f"日常_玄荒：退出起点 #{landing.id} 不支持，停止")
        landing = yield from context.wait_scene(
            [34, 55], wait=timeout,
            label="日常_玄荒：等待最终退出落点（离开确认由弹窗守护处理）",
        )
        if landing.id not in (420, 85) or leave_round:
            break
        landing = yield from context.wait_scene(
            [420, 85], wait=timeout, label="日常_玄荒：离开未生效，复核仍在副本",
        )
    if landing.id == 55:
        yield from context.go_scene(34)
        landing = yield from context.wait_scene(
            [34], wait=timeout, label="日常_玄荒：验证返回世界 #34",
        )
    if landing.id != 34:
        raise RuntimeError(f"日常_玄荒：退出未到世界 #34，实际 #{landing.id}，停止")
    return landing


class DailyXuanhuangTaskMixin:
    def _daily_xuanhuang_target_snapshot(self) -> dict[str, Any]:
        from backend.core.fanxiu.instrumentation.daily_task import read_daily_task_snapshot
        snapshot = read_daily_task_snapshot(XUANHUANG_DAILY_TASK_ID)
        xuanhuang_daily_target_complete(snapshot)
        return snapshot

    def _daily_xuanhuang_finish_reward_clear(self, context: Any, rounds_completed: int):
        """奖励耗尽只结束奖励挑战；日常缺口独立报告，避免无效无奖励战斗。

        2026-10-11 实机：无奖励进入后首领停在20%，客户端提示次数已满，
        无法对血量20%以下的首领造成伤害，Quest1049保持2/3。
        因而缺口需奖励次数补给能力，不能靠确认无奖励进入补足。
        """
        daily = self._daily_xuanhuang_target_snapshot()
        done = xuanhuang_daily_target_complete(daily)
        from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
        record_world_discovery("daily_xuanhuang", {
            "updated_at": time.time(),
            "scheduler_task_id": "daily-xuanhuang", "business_complete": done,
            "daily_target": daily, "reward_chances_remaining": 0,
            "reason": "日常击杀目标已完成" if done else "奖励次数耗尽，需补给，日常目标未满",
        })
        context.set_next_time(next_daily_xuanhuang_time())
        yield from context.go_scene(34)
        return {
            # Scheduler success means this trigger finished normally. Actual
            # business completion remains explicit in outcome/daily_target.
            "result": "success",
            "outcome": "daily_target_and_rewards_complete" if done else "reward_supply_required",
            "completion_scope": "daily_target_and_reward_chances" if done else "reward_chances",
            "reward_chances_remaining": 0, "daily_task_id": XUANHUANG_DAILY_TASK_ID,
            "daily_target_complete": done, "daily_target": daily,
            "rounds_completed": rounds_completed, "current_scene": 34,
            "message": ("日常_玄荒：日常击杀目标已完成且奖励次数耗尽" if done else
                        f"日常_玄荒：奖励次数耗尽，日常击杀仍为{daily['turn']}/{daily['target_turn']}；"
                        "无奖励进入受20%血量伤害上限限制，需补充奖励次数，未完成日常目标"),
        }

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
        context: Any,
        *,
        payload: dict[str, Any] | None,
        view_timeout: float,
        recommend_timeout_seconds: float,
    ):
        yield from context.go_scene(69)
        entry_result = yield from context.open_daily_entry(
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

        yield from context.wait_scene_exact(
            [400],
            timeout=view_timeout,
            label="日常_玄荒：等待玄荒入口 #400",
        )
        yield from context.wait_click(
            400,
            "玄荒",
            timeout=view_timeout,
        )
        yield from context.wait_scene_exact(
            [417],
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

        match = yield from context.wait_ocr_any_text(
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
            yield from context.wait_click(
                417,
                "2级",
                timeout=view_timeout,
            )
            match = yield from context.wait_ocr_any_text(
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
        # 推荐标记可能贴左边；x减文字宽度会落到屏幕外。OCR坐标为像素，
        # 取标记下方首领头像中心，不能混用Shape的归一化坐标。
        click_x, click_y = match.point(anchor="bottom_center", offset=(0, 0.8), offset_unit="height")
        frame = context.view(417).raw
        if not (0 <= click_x < frame["width"] and 0 <= click_y < frame["height"]):
            raise RuntimeError("日常_玄荒：推荐首领点击点超出当前帧，停止")
        context.click_frame_point(417, click_x, click_y)

        yield from context.wait_scene_exact(
            [418],
            timeout=view_timeout,
            observation_scenes=[417],
            label="日常_玄荒：等待挑战页 #418",
        )
        return "open"

    def _daily_xuanhuang_read_remaining(
        self,
        context: Any,
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
            frame = context.cur_frame(update=True)
            _numbers, last_text = context.ocr_numbers_in_shapes(
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
                yield from context.wait_action_settle(retry_seconds)
        raise RuntimeError(
            f"日常_玄荒：无法从 #418[次数] 稳定识别分子/分母，OCR={last_text!r}"
        )

    def _daily_xuanhuang_wait_battle_done(
        self,
        context: Any,
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
            _wait_scene_match = yield from context.wait_scene([*XUANHUANG_BATTLE_SCENES, 420], wait=5.0, required=False)
            (scene_id, _score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id == 420:
                return saw_battle_scene
            if scene_id in XUANHUANG_BATTLE_SCENES:
                saw_battle_scene = True
                deadline = time.monotonic() + timeout_seconds
                # Same evidence renews the outer Job budget: a healthy long
                # fight keeps the device; losing the battle frame lets the
                # lease lapse within timeout_seconds (+ margin).
                renew = getattr(self, "renew_job_liveness_lease", None)
                if callable(renew):
                    renew(timeout_seconds + 30.0, f"玄荒战斗画面 #{scene_id}")
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
                    status_persister = getattr(self, "_persist_status", None)
                    if callable(status_persister):
                        status_persister(min_interval_seconds=2.0)
                next_status_at = now + 30.0
            if now >= deadline:
                _wait_scene_match = yield from context.wait_scene([*XUANHUANG_BATTLE_SCENES, 420], wait=5.0, required=False)
                (final_scene, _score, final_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if final_scene == 420:
                    return saw_battle_scene
                if final_scene in XUANHUANG_BATTLE_SCENES:
                    saw_battle_scene = True
                    deadline = time.monotonic() + timeout_seconds
                    renew = getattr(self, "renew_job_liveness_lease", None)
                    if callable(renew):
                        renew(timeout_seconds + 30.0, f"玄荒战斗画面 #{final_scene}")
                    yield from context.wait_action_settle(poll_seconds)
                    continue
                raise TimeoutError(
                    "日常_玄荒：连续无法识别玄荒战斗或结算 #420 "
                    f"超过 {timeout_seconds:g} 秒"
                )
            yield from context.wait_action_settle(poll_seconds)

    def _daily_xuanhuang_leave_finished_battle(
        self,
        context: Any,
        *,
        view_timeout: float,
    ):
        return (yield from leave_xuanhuang(context, view_timeout=view_timeout))

    def _run_daily_xuanhuang_flow(
        self,
        context: Any,
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
            # frames renew this deadline and the outer Job liveness lease; the
            # Job's absolute ceiling still bounds a genuinely endless fight.
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
            _wait_scene_match = yield from context.wait_scene(wait=5.0, required=False)
            (current_scene, _score, _frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if current_scene == 418:
                start_from_counter = True
                break
            if current_scene in XUANHUANG_BATTLE_SCENES | {420}:
                resume_battle_scene = current_scene
                break
            if current_scene is not None:
                break
            if attempt + 1 < current_scene_probe_attempts:
                yield from context.wait_action_settle(current_scene_probe_retry_seconds)

        if current_scene is None:
            # A long #186/#419 battle can finish between Scheduler attempts.
            # Its departure briefly renders a blank transition frame with no
            # actionable control. Treating that frame as a missing route makes
            # go_scene(34) fail before the world page has time to appear.
            # Wait only for existing safe anchors; this adds no asset identity
            # and performs no irreversible click during the transition.
            resumed = yield from context.wait_scene(
                [34,
                418,
                186,
                419,
                420,
                85,
                678,
                395,
                55],
                wait=resume_transition_timeout_seconds,
                label="日常_玄荒：等待战斗退出过渡落到已知页面",
            )
            current_scene = resumed.id
            if current_scene == 418:
                start_from_counter = True
            elif current_scene in XUANHUANG_BATTLE_SCENES | {420}:
                resume_battle_scene = current_scene
            elif current_scene in {395, 55}:
                yield from context.go_scene(34)

        while True:
            # A settled round has already loaded this period's counter. The
            # final round can remove its daily-list entry, so navigating back
            # through open_daily_entry first needlessly scans the whole list
            # (live 2026-10-10: last remaining=1, #420 -> #34, native=0).
            # This shortcut is only after this attempt observed settlement;
            # This is the reward counter, not the daily participation target.
            # Unrewarded entry cannot kill below20%; report the deficit separately.
            # initial entry still naturally loads the module, and resumed
            # battles must finish before their counter can close the Job.
            if rounds_completed and resume_battle_scene is None:
                settled = self._daily_xuanhuang_runtime_snapshot(payload)
                if (settled.get("complete") is True
                        and settled.get("counter_loaded") is True
                        and settled.get("remaining") == 0):
                    return (yield from self._daily_xuanhuang_finish_reward_clear(context, rounds_completed))
            if resume_battle_scene is not None:
                if resume_battle_scene != 420:
                    resumed_counter = self._daily_xuanhuang_runtime_snapshot(payload)
                    if (resumed_counter.get("complete") is True
                            and resumed_counter.get("counter_loaded") is True
                            and resumed_counter.get("remaining") == 0):
                        # Re-entry into an unrewarded fight must not wait for
                        # an impossible kill/settlement below20% HP. Current
                        # Quest facts decide completed versus supply-required.
                        return (yield from self._daily_xuanhuang_finish_reward_clear(context, rounds_completed))
                    yield from self._daily_xuanhuang_wait_battle_done(
                        context,
                        timeout_seconds=battle_timeout_seconds,
                        poll_seconds=battle_poll_seconds,
                    )
                yield from self._daily_xuanhuang_leave_finished_battle(
                    context,
                    view_timeout=view_timeout,
                )
                rounds_completed += 1
                resume_battle_scene = None
                continue
            if not start_from_counter:
                yield from context.go_scene(34)
                counter_state = yield from self._daily_xuanhuang_open_counter(
                    context,
                    payload=payload,
                    view_timeout=view_timeout,
                    recommend_timeout_seconds=recommend_timeout_seconds,
                )
                if counter_state == "done":
                    return (yield from self._daily_xuanhuang_finish_reward_clear(context, rounds_completed))
            start_from_counter = False
            remaining = yield from self._daily_xuanhuang_read_remaining(
                context,
                payload=payload,
                attempts=counter_attempts,
                retry_seconds=counter_retry_seconds,
            )
            if remaining == 0:
                return (yield from self._daily_xuanhuang_finish_reward_clear(context, rounds_completed))
            if rounds_completed >= max_rounds:
                raise RuntimeError(
                    f"日常_玄荒：已挑战 {rounds_completed} 轮但剩余次数仍为 {remaining}"
                )

            # #418 的「前往」偶发会吞掉一次点击。不能在没有确认页面状态的
            # 情况下直接补点（挑战次数属于不可逆动作）；复用 Runtime 的
            # wait_click_then_scene：只有新帧仍可靠识别为源场景 #418 时才重试，
            # 一旦已进入 #419/#420 或变成 unknown 就停止补点。
            yield from context.wait_click_then_scene(
                418,
                "前往",
                *XUANHUANG_BATTLE_SCENES,
                420,
                timeout=battle_entry_timeout_seconds,
                settle_seconds=1.0,
                retry_if_source_remains=True,
                max_clicks=battle_entry_max_clicks,
                label="日常_玄荒：点击前往后等待玄荒战斗或结算 #420",
            )
            yield from self._daily_xuanhuang_wait_battle_done(
                context,
                timeout_seconds=battle_timeout_seconds,
                poll_seconds=battle_poll_seconds,
            )
            yield from self._daily_xuanhuang_leave_finished_battle(
                context,
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
            yield from self._execute_daily_task(
                ctx,
                stop_event,
                payload,
                task_type="daily_xuanhuang",
                label="日常_玄荒",
                flow=lambda context: self._run_daily_xuanhuang_flow(context, payload),
            )
        )
