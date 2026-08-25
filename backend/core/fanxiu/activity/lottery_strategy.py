from __future__ import annotations

"""Pure draw policy shared by limited-resource lottery activities.

The policy deliberately knows nothing about scenes, buttons, Runtime readers,
or persistence.  Activity adapters supply one coherent snapshot and execute the
single returned action.  This keeps the irreversible draw decision testable
without an emulator.
"""

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence


LotteryGoalKind = Literal["first_hit", "exhaust_all", "target_count"]
LotteryRemainderMode = Literal["single", "capped_ten", "defer"]


class LotteryStrategyError(ValueError):
    """The supplied business state cannot authorize a draw safely."""


@dataclass(frozen=True)
class LotteryGoal:
    kind: LotteryGoalKind
    target_count: int | None = None

    def resolved_target_count(self) -> int | None:
        if self.kind == "first_hit":
            if self.target_count not in (None, 1):
                raise LotteryStrategyError("first_hit 的 target_count 只能为 1")
            return 1
        if self.kind == "target_count":
            return _positive_int(self.target_count, "target_count")
        if self.kind == "exhaust_all":
            if self.target_count is not None:
                raise LotteryStrategyError("exhaust_all 不接受 target_count")
            return None
        raise LotteryStrategyError(f"未知抽奖目标：{self.kind}")


@dataclass(frozen=True)
class LotteryPolicy:
    goal: LotteryGoal
    normal_batch_size: int = 10
    remainder_mode: LotteryRemainderMode = "single"
    top_up_positive_refund_after_goal: bool = False

    def validate(self) -> None:
        self.goal.resolved_target_count()
        if _positive_int(self.normal_batch_size, "normal_batch_size") != 10:
            # Bothdraw currently exposes a ten-draw toggle.  Refuse to make a
            # generic-looking policy silently authorize an unverified size.
            raise LotteryStrategyError("当前通用策略只支持已验收的十连批次")
        if self.remainder_mode not in {"single", "capped_ten", "defer"}:
            raise LotteryStrategyError(f"未知终局零头策略：{self.remainder_mode}")


@dataclass(frozen=True)
class LotteryMilestone:
    threshold: int
    reward_draws: int
    state: str = "locked"
    reward_id: int | None = None


@dataclass(frozen=True)
class LotteryDecision:
    action: Literal["claim_rewards", "draw", "stop"]
    reason: str
    draw_mode: Literal["ten_draw", "single_draw"] | None = None
    requested_batch_size: int = 0
    expected_batch_size: int = 0
    target_threshold: int | None = None
    stop_reason: str | None = None


def decide_lottery_action(
    snapshot: Mapping[str, Any],
    *,
    policy: LotteryPolicy,
    milestones: Sequence[LotteryMilestone] = (),
) -> LotteryDecision:
    """Return exactly one safe action from a coherent cumulative snapshot.

    ``hit_count`` is the cumulative count for the policy's selected target (or
    the activity-wide grand-prize count for a fixed pool).  Milestone rewards
    are already-normalized draw counts; parsing game reward strings belongs to
    the activity adapter.
    """

    policy.validate()
    if snapshot.get("complete") is not True:
        raise LotteryStrategyError(
            str(snapshot.get("reason") or "抽奖运行态数据不完整")
        )
    available = _nonnegative_int(snapshot.get("available_draws"), "available_draws")
    progress = _nonnegative_int(snapshot.get("progress"), "progress")
    hit_count = _nonnegative_int(snapshot.get("hit_count"), "hit_count")
    if hit_count > progress:
        raise LotteryStrategyError(
            f"命中累计不能超过抽奖进度：hit_count={hit_count}, progress={progress}"
        )

    if _has_claimable(snapshot.get("claimable")):
        return LotteryDecision("claim_rewards", "存在已达成且尚未领取的累抽奖励")

    target_count = policy.goal.resolved_target_count()
    goal_reached = target_count is not None and hit_count >= target_count
    if goal_reached:
        if policy.top_up_positive_refund_after_goal:
            target = _next_positive_refund(
                milestones,
                progress=progress,
                available_draws=available,
            )
            if target is not None:
                gap = target.threshold - progress
                return LotteryDecision(
                    "draw",
                    f"目标已达成；单抽补{gap}抽到净增{target.reward_draws - gap}抽的档位",
                    draw_mode="single_draw",
                    requested_batch_size=1,
                    expected_batch_size=1,
                    target_threshold=target.threshold,
                )
        return LotteryDecision(
            "stop",
            f"抽奖目标已达成：{hit_count}/{target_count}",
            stop_reason=(
                "first_hit_reached"
                if policy.goal.kind == "first_hit"
                else "target_count_reached"
            ),
        )

    if available == 0:
        return LotteryDecision(
            "stop",
            "当前可用抽数已经耗尽",
            stop_reason=(
                "draws_exhausted"
                if policy.goal.kind == "exhaust_all"
                else "draws_exhausted_before_target"
            ),
        )

    batch_size = policy.normal_batch_size
    if available >= batch_size:
        return LotteryDecision(
            "draw",
            "库存足够，执行正常十连",
            draw_mode="ten_draw",
            requested_batch_size=batch_size,
            expected_batch_size=batch_size,
        )

    if policy.remainder_mode == "defer":
        return LotteryDecision(
            "stop",
            f"保留终局零头 {available} 抽给后续阶段",
            stop_reason="terminal_remainder_deferred",
        )
    if policy.remainder_mode == "capped_ten":
        return LotteryDecision(
            "draw",
            f"继续请求十连，由服务端按终局零头 {available} 抽结算",
            draw_mode="ten_draw",
            requested_batch_size=batch_size,
            expected_batch_size=available,
        )
    return LotteryDecision(
        "draw",
        f"库存不足十连，逐次处理终局零头 {available} 抽",
        draw_mode="single_draw",
        requested_batch_size=1,
        expected_batch_size=1,
    )


def _next_positive_refund(
    milestones: Sequence[LotteryMilestone],
    *,
    progress: int,
    available_draws: int,
) -> LotteryMilestone | None:
    candidates: list[LotteryMilestone] = []
    for milestone in milestones:
        threshold = _positive_int(milestone.threshold, "milestone.threshold")
        reward_draws = _nonnegative_int(
            milestone.reward_draws, "milestone.reward_draws"
        )
        if milestone.state != "locked" or threshold <= progress:
            continue
        gap = threshold - progress
        # "Positive" is strict: spending four to receive four is break-even,
        # not evidence authorizing an otherwise completed lottery to continue.
        if gap <= available_draws and reward_draws > gap:
            candidates.append(milestone)
    return min(candidates, key=lambda item: item.threshold) if candidates else None


def _has_claimable(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    raise LotteryStrategyError("claimable 必须是布尔值或奖励集合")


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LotteryStrategyError(f"{field} 必须是非负整数：{value!r}")
    return value


def _positive_int(value: Any, field: str) -> int:
    result = _nonnegative_int(value, field)
    if result <= 0:
        raise LotteryStrategyError(f"{field} 必须是正整数：{value!r}")
    return result


__all__ = [
    "LotteryDecision",
    "LotteryGoal",
    "LotteryGoalKind",
    "LotteryMilestone",
    "LotteryPolicy",
    "LotteryRemainderMode",
    "LotteryStrategyError",
    "decide_lottery_action",
]
