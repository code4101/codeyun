"""日常、周常活跃度领取流程及业务复查时间。"""
from __future__ import annotations
from backend.core.fanxiu.data_annotation.effective_time import job_now
import threading
from datetime import datetime, timedelta
from typing import Any, Mapping
from backend.core.fanxiu.catalog.weekly_activity import WEEKLY_ACTIVITY_REWARD_MILESTONES
from .weekly_activity_observations import (
    weekly_activity_pending_badge_present,
    weekly_activity_reward_layout_from_ocr,
    detect_weekly_activity_reward_states,
)

DAILY_ACTIVITY_OCR_MAX_ATTEMPTS = 5


def read_weekly_activity_runtime_snapshot() -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.weekly_activity import (
        read_weekly_activity_snapshot,
    )

    return read_weekly_activity_snapshot()


class ActivityRewardsTaskMixin:
    def _next_daily_activity_time_text(self) -> str:
        now = job_now()
        next_at = (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
        return next_at.strftime("%Y-%m-%d %H:%M:%S")

    def daily_activity_flow(self, context: Any):
        yield from context.go_scene(69)

        attempt = 0
        while attempt < DAILY_ACTIVITY_OCR_MAX_ATTEMPTS:
            attempt += 1
            _wait_scene_match = yield from context.wait_scene([69], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 69:
                raise RuntimeError(f"日常_活跃度：读取总活跃度时已不在 #69：#{scene_id or 'unknown'} {score:.0f}%")

            values, text = context.ocr_numbers_in_shapes(
                69,
                ["总活跃度"],
                padding=0,
                frame_data_url=frame,
            )
            if values:
                total_activity = int(values[0])
                self._log("detail", f"日常_活跃度：第 {attempt} 次读取总活跃度={total_activity}，OCR={text!r}")
                break

            if attempt < DAILY_ACTIVITY_OCR_MAX_ATTEMPTS:
                self._log("detail", f"日常_活跃度：第 {attempt} 次未读到总活跃度数值，OCR={text!r}，继续识别")
                yield from context.wait_action_settle(0.8)
            else:
                self._log("detail", f"日常_活跃度：第 {attempt} 次仍未读到总活跃度数值，OCR={text!r}，停止本次识别")
        else:
            raise RuntimeError(
                "日常_活跃度：连续 "
                f"{DAILY_ACTIVITY_OCR_MAX_ATTEMPTS} 次未读到总活跃度数值，停止本次作业等待技术重试"
            )

        if total_activity < 500:
            yield from context.go_scene(34)
            context.set_next_time(
                (job_now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
            )
            return {
                "result": "success",
                "message": f"日常_活跃度：总活跃度 {total_activity} < 500，已回到世界，1 小时后重试",
                "current_scene": 34,
            }

        context.click_shape_center(69, "奖励")
        yield from context.wait_action_settle(1.5)
        yield from context.go_scene(34)
        context.set_next_time(self._next_daily_activity_time_text())
        return {
            "result": "success",
            "message": f"日常_活跃度：总活跃度 {total_activity}，已点击奖励并回到世界",
            "current_scene": 34,
        }

    def _execute_daily_activity_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        normalized_payload = dict(payload or {})
        normalized_payload.setdefault("fallback_seconds", 3600)
        result = yield from self._execute_daily_task(
            ctx,
            stop_event,
            normalized_payload,
            task_type="daily_activity",
            label="日常_活跃度",
            flow=self.daily_activity_flow,
        )
        return result

    def _next_weekly_activity_time_text(
        self,
        *,
        completed: bool,
        now: datetime | None = None,
    ) -> str:
        current = now or job_now()
        if not completed and current.weekday() in {3, 4}:
            next_at = current + timedelta(days=1)
        else:
            days_until_thursday = (3 - current.weekday()) % 7
            if days_until_thursday == 0:
                days_until_thursday = 7
            next_at = current + timedelta(days=days_until_thursday)
        return next_at.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

    def weekly_activity_flow(self, context: Any):
        def read_reward_layout(frame: str):
            """Read the #402 reward rail over bounded fresh frames.

            页签切换、横向滚动和点击档位之后，奖励轨道都有自己的入场/惯性
            动画：某一帧可能只渲染出部分档位标签，直接判定"标签不完整"会把
            瞬态动画当成资产缺陷（2026-09-18 真实失败：只读到 [1200]）。
            这里只对"标签不足"这类瞬态结果重取帧，越界、重复档位等结构性
            异常仍然立即失败，保持原有的失败关闭语义。
            """

            transient_markers = (
                "未识别到奖励轨道档位标签",
                "档位标签不完整或顺序异常",
                "档位标签识别到未知档",
            )
            attempts = 4
            last_error: RuntimeError | None = None
            current_frame = frame
            for attempt in range(attempts):
                try:
                    layout = weekly_activity_reward_layout_from_ocr(
                        context.full_frame_ocr_tokens(current_frame),
                        frame_width=900,
                        frame_height=1600,
                    )
                    # 档位状态是在同一帧上按投影点取色的，必须把实际用于
                    # 投影的那一帧一起交回调用方，避免用旧帧判色。
                    return layout, current_frame
                except RuntimeError as exc:
                    if not any(marker in str(exc) for marker in transient_markers):
                        raise
                    last_error = exc
                    if attempt + 1 >= attempts:
                        break
                    self._log(
                        "detail",
                        f"周常_活跃度：奖励轨道标签尚未渲染完整，重新取帧复核"
                        f"（第 {attempt + 1} 次）",
                    )
                    yield from context.wait_action_settle(0.8)
                    current_frame = context.cur_frame(update=True)
            assert last_error is not None
            raise last_error

        yield from context.go_scene(69)
        context.click_shape_center(69, "周常")
        yield from context.wait_action_settle(float(context.payload.get("weekly_tab_settle_seconds") or 1.5))

        attempt = 0
        while True:
            attempt += 1
            _wait_scene_match = yield from context.wait_scene([402], wait=5.0, required=False)
            (scene_id, score, frame) = (
                (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
            )
            if scene_id != 402:
                self._log(
                    "detail",
                    f"周常_活跃度：第 {attempt} 次尚未识别到 #402：#{scene_id or 'unknown'} {score:.0f}%，继续识别",
                )
                yield from context.wait_action_settle(0.8)
                continue

            values, text = context.ocr_numbers_in_shapes(
                402,
                ["活跃度"],
                padding=0,
                frame_data_url=frame,
            )
            if values:
                total_activity = int(values[0])
                self._log(
                    "detail",
                    f"周常_活跃度：第 {attempt} 次读取活跃度={total_activity}，OCR={text!r}",
                )
                break

            self._log(
                "detail",
                f"周常_活跃度：第 {attempt} 次未读到活跃度数值，OCR={text!r}，继续识别",
            )
            yield from context.wait_action_settle(0.8)

        threshold = max(1, int(context.payload.get("weekly_activity_threshold") or 2400))
        now = job_now()
        if total_activity < threshold:
            final_attempt = now.weekday() == 5
            next_time = self._next_weekly_activity_time_text(completed=final_attempt, now=now)
            if final_attempt:
                message = (
                    f"周常_活跃度：周六最终检查 {total_activity} < {threshold}，"
                    f"本周结束，下次 {next_time}"
                )
            else:
                message = f"周常_活跃度：活跃度 {total_activity} < {threshold}，下次 {next_time} 复查"
            yield from context.go_scene(34)
            context.set_next_time(next_time)
            return {
                "result": "success",
                "message": message,
                "current_scene": 34,
            }

        reward_layout, frame = yield from read_reward_layout(frame)
        reward_states = detect_weekly_activity_reward_states(frame, reward_layout)
        runtime_snapshot = read_weekly_activity_runtime_snapshot()
        if runtime_snapshot.get("complete") is not True:
            raise RuntimeError(
                f"周常_活跃度：Runtime 权威领取集合不完整："
                f"{runtime_snapshot.get('reason') or runtime_snapshot.get('status') or 'unknown'}"
            )
        if tuple(runtime_snapshot.get("thresholds") or ()) != WEEKLY_ACTIVITY_REWARD_MILESTONES:
            raise RuntimeError(f"周常_活跃度：Runtime 档位全集漂移：{runtime_snapshot.get('thresholds')}")
        if int(runtime_snapshot.get("active_num") or -1) != total_activity:
            raise RuntimeError(
                f"周常_活跃度：GUI/Runtime 活跃度不一致："
                f"GUI={total_activity} Runtime={runtime_snapshot.get('active_num')}"
            )

        def validate_gui_cross_check(snapshot: Mapping[str, Any], states: Mapping[int, Mapping[str, Any]]) -> None:
            claimed = {int(value) for value in snapshot.get("claimed_thresholds") or []}
            claimable = {int(value) for value in snapshot.get("claimable_thresholds") or []}
            disagreements: list[str] = []
            for milestone, row in states.items():
                if milestone > total_activity:
                    continue
                expected = "claimed" if milestone in claimed else "claimable" if milestone in claimable else "unknown"
                if row.get("state") != expected:
                    disagreements.append(f"{milestone}:{row.get('state')}!=Runtime-{expected}")
            if disagreements:
                raise RuntimeError(f"周常_活跃度：GUI/Runtime 档位状态不一致：{disagreements}")

        validate_gui_cross_check(runtime_snapshot, reward_states)

        def confirm_reward_scene(*, action_label: str):
            """Allow the reward page a few frames to settle without repeating the action."""
            latest_frame = ""
            latest_scene_id: int | None = None
            latest_score = 0.0
            for attempt in range(3):
                _wait_scene_match = yield from context.wait_scene([402], label=f'周常_活跃度：{action_label}后复核奖励页', wait=5.0, required=False)
                (latest_scene_id, latest_score, latest_frame) = (
                    (_wait_scene_match.scene_id, _wait_scene_match.score, _wait_scene_match.frame_data_url)
                    if _wait_scene_match is not None else (None, 0.0, context.frame_data_url or "")
                )
                if latest_scene_id == 402:
                    return latest_frame
                if attempt < 2:
                    self._log(
                        "detail",
                        f"周常_活跃度：{action_label}后第 {attempt + 1} 帧暂未识别 #402，继续复核",
                    )
                    yield from context.wait_action_settle(0.4)
            raise RuntimeError(
                f"周常_活跃度：{action_label}后未留在 #402："
                f"#{latest_scene_id or 'unknown'} {latest_score:.0f}%"
            )

        claimed_now: list[int] = []
        scroll_attempts = 0
        final_frame = frame
        final_milestone = max(WEEKLY_ACTIVITY_REWARD_MILESTONES)
        while True:
            claimable_thresholds = [
                int(value) for value in runtime_snapshot.get("claimable_thresholds") or []
            ]
            if not claimable_thresholds and final_milestone in reward_states:
                break
            validate_gui_cross_check(runtime_snapshot, reward_states)
            visible_claimable = [
                milestone for milestone in claimable_thresholds if milestone in reward_states
            ][:1]
            # 每次动作后重算候选；刷新 Runtime 不会使旧列表自动失效。
            # 全部已领时仍滚到最高档，完成页面终态核验。
            if not visible_claimable:
                if scroll_attempts >= 4:
                    raise RuntimeError(
                        f"周常_活跃度：横向滚动后仍未找到可领档 {claimable_thresholds}"
                    )
                before_visible = set(reward_states)
                context.drag_frame_point(402, 760, 350, 260, 350, duration_ms=1000)
                yield from context.wait_action_settle(0.8)
                final_frame = yield from confirm_reward_scene(action_label="横向滚动")
                reward_layout, final_frame = yield from read_reward_layout(final_frame)
                reward_states = detect_weekly_activity_reward_states(final_frame, reward_layout)
                scroll_attempts += 1
                if set(reward_states) == before_visible:
                    raise RuntimeError(
                        f"周常_活跃度：横向滚动后可见档位未推进：{sorted(before_visible)}"
                    )
                continue

            for milestone in visible_claimable:
                before = reward_states[milestone]
                click_x, click_y = before["point"]
                context.click_frame_point(402, click_x, click_y)
                yield from context.wait_action_settle(float(context.payload.get("reward_settle_seconds") or 1.5))
                after_frame = yield from confirm_reward_scene(
                    action_label=f"点击 {milestone} 档",
                )
                after_layout, after_frame = yield from read_reward_layout(after_frame)
                if milestone not in after_layout:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后该档已离开可见轨道，无法复验")
                after_states = detect_weekly_activity_reward_states(after_frame, after_layout)
                if after_states[milestone]["state"] != "claimed":
                    raise RuntimeError(
                        f"周常_活跃度：点击 {milestone} 档后未复验为绿色勾："
                        f"{after_states[milestone]['state']}"
                    )
                runtime_snapshot = read_weekly_activity_runtime_snapshot()
                if runtime_snapshot.get("complete") is not True:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后 Runtime 快照不完整")
                if milestone not in {int(value) for value in runtime_snapshot.get("claimed_thresholds") or []}:
                    raise RuntimeError(f"周常_活跃度：点击 {milestone} 档后 Runtime 未确认该档已领取")
                validate_gui_cross_check(runtime_snapshot, after_states)
                claimed_now.append(milestone)
                reward_states = after_states
                final_frame = after_frame
        remaining_claimable = [int(value) for value in runtime_snapshot.get("claimable_thresholds") or []]
        if remaining_claimable:
            raise RuntimeError(f"周常_活跃度：领取后仍有 Runtime 可领档：{remaining_claimable}")
        if reward_states.get(final_milestone, {}).get("state") != "claimed":
            raise RuntimeError(f"周常_活跃度：右边界 {final_milestone} 档未显示绿色勾")
        final_tokens = context.full_frame_ocr_tokens(final_frame)
        if weekly_activity_pending_badge_present(
            final_tokens,
            frame_width=900,
            frame_height=1600,
        ):
            raise RuntimeError("周常_活跃度：Runtime 无可领档但周常页签仍显示“领”，拒绝写入下周")
        yield from context.go_scene(34)
        next_time = self._next_weekly_activity_time_text(completed=True, now=now)
        context.set_next_time(next_time)
        reward_message = (
            f"本次领取 {claimed_now}"
            if claimed_now
            else "全部达标档位已领取，零点击幂等结束"
        )
        return {
            "result": "success",
            "message": (
                f"周常_活跃度：活跃度 {total_activity} >= {threshold}，"
                f"{reward_message}，下次 {next_time}"
            ),
            "current_scene": 34,
        }

    def _execute_weekly_activity_task(
        self,
        ctx: dict[str, Any],
        stop_event: threading.Event,
        payload: dict[str, Any] | None = None,
    ):
        return (yield from self._execute_daily_task(
            ctx,
            stop_event,
            payload,
            task_type="weekly_activity",
            label="周常_活跃度",
            flow=self.weekly_activity_flow,
        ))
