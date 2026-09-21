from __future__ import annotations

"""Slim pure policy resolution for theme-collection lottery tickets.

The draw algorithm itself stays in :mod:`backend.core.fanxiu.activity.
lottery_strategy`; this module only decides which :class:`LotteryPolicy` the
adapter may execute, or which business fact is still missing.  It never reads
Runtime, never persists, and never invents an authorization the user did not
state.

Ticket handling split by cross-period retainability evidence:

* ``retainable``     -> default ``first_hit`` (stop after one grand prize).
* ``non_retainable`` -> default ``exhaust_all`` (use up this period).
* ``unknown``        -> pending evidence; never silently treated as either.

Evidence interpretation: an explicit statement that tickets can be used in a
later occurrence establishes retainability. End-of-event automatic use,
recycling or conversion establishes non-retainability; binding only describes
the resulting currency, not permission to carry tickets into the next event.
The absence of an expiry date alone does not establish retainability.

Tickets that share one retainability may be aggregated by the adapter.  Tickets
with different policies require the adapter to prove an executable balance
scope; a guessed priority list is not accepted.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Literal, Sequence

from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryPolicy,
    LotteryRemainderMode,
    LotteryStrategyError,
)


TicketRetainability = Literal["retainable", "non_retainable", "unknown"]
_RETAINABILITIES = frozenset({"retainable", "non_retainable", "unknown"})
_DEFAULT_FINAL_DAY_TAIL_AFTER = time(21, 0)


@dataclass(frozen=True)
class ThemeLotteryTicket:
    """One draw ticket and the evidence for its cross-period retainability."""

    item_id: int
    label: str
    retainability: TicketRetainability
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, int) or isinstance(self.item_id, bool):
            raise ValueError("抽奖券 item_id 必须为整数")
        if int(self.item_id) <= 0:
            raise ValueError("抽奖券 item_id 必须为正整数")
        if self.retainability not in _RETAINABILITIES:
            raise ValueError(f"未知留存性：{self.retainability}")


@dataclass(frozen=True)
class ThemeLotteryPolicyResolution:
    status: Literal["ready", "pending_evidence"]
    reason: str
    policy: LotteryPolicy | None = None
    missing_evidence: tuple[str, ...] = ()
    aggregate_retainability: TicketRetainability | None = None

    def __post_init__(self) -> None:
        if self.status == "ready" and self.policy is None:
            raise ValueError("ready 的抽奖策略必须携带 LotteryPolicy")
        if self.status == "pending_evidence" and self.policy is not None:
            raise ValueError("pending_evidence 不得携带已解析策略")


@dataclass(frozen=True)
class ThemeLotteryPhase:
    status: Literal["ready", "pending_evidence", "expired"]
    reason: str
    policy: LotteryPolicy | None = None
    remainder_mode: LotteryRemainderMode | None = None
    next_time: datetime | None = None
    missing_evidence: tuple[str, ...] = ()


def resolve_theme_lottery_policy(
    tickets: Sequence[ThemeLotteryTicket],
    *,
    goal_override: LotteryGoal | None = None,
    non_retainable_remainder_mode: LotteryRemainderMode = "defer",
) -> ThemeLotteryPolicyResolution:
    """Resolve one executable policy, or the exact missing fact.

    ``goal_override`` is the explicit user whitelist replacing the default goal
    (for example ``LotteryGoal("target_count", target_count=2)``); it is never
    an unbounded "allow extra draws" switch.  A non-retainable remainder
    defaults to ``defer``; the phase helper switches it to ``single`` after the
    final-day tail time.
    """

    rows = tuple(tickets)
    if not rows:
        return ThemeLotteryPolicyResolution(
            status="pending_evidence",
            reason="未提供任何抽奖券证据",
            missing_evidence=("no_tickets",),
        )

    unknown = tuple(
        ticket.item_id for ticket in rows if ticket.retainability == "unknown"
    )
    if unknown:
        return ThemeLotteryPolicyResolution(
            status="pending_evidence",
            reason="存在跨期留存性未经证实的抽奖券",
            missing_evidence=tuple(
                f"ticket_retainability:{item_id}" for item_id in unknown
            ),
        )

    retainabilities = {ticket.retainability for ticket in rows}
    if len(retainabilities) > 1:
        return ThemeLotteryPolicyResolution(
            status="pending_evidence",
            reason=(
                "留存与不可留存券并存，且消费次序未证实；"
                "需适配器提供可执行余额范围"
            ),
            missing_evidence=("mixed_ticket_execution_scope_unproven",),
        )

    aggregate = next(iter(retainabilities))
    if goal_override is not None:
        goal = goal_override
    elif aggregate == "retainable":
        goal = LotteryGoal("first_hit")
    else:
        goal = LotteryGoal("exhaust_all")
    remainder_mode: LotteryRemainderMode = (
        "single" if aggregate == "retainable" else non_retainable_remainder_mode
    )
    policy = LotteryPolicy(goal=goal, remainder_mode=remainder_mode)
    try:
        policy.validate()
    except LotteryStrategyError as exc:
        return ThemeLotteryPolicyResolution(
            status="pending_evidence",
            reason=f"目标不满足现有抽奖策略约束：{exc}",
            missing_evidence=("goal_not_supported",),
        )
    return ThemeLotteryPolicyResolution(
        status="ready",
        reason=f"{aggregate} 券解析为 {goal.kind} 策略",
        policy=policy,
        aggregate_retainability=aggregate,
    )


def theme_lottery_tail_at(
    *,
    final_day: date,
    now: datetime,
    final_day_tail_after: time = _DEFAULT_FINAL_DAY_TAIL_AFTER,
) -> datetime:
    if now.tzinfo is None:
        raise ValueError("主题集抽奖时钟必须带时区")
    return datetime.combine(final_day, final_day_tail_after, tzinfo=now.tzinfo)


def resolve_theme_lottery_phase(
    resolution: ThemeLotteryPolicyResolution,
    *,
    final_day: date,
    now: datetime,
    final_day_tail_after: time = _DEFAULT_FINAL_DAY_TAIL_AFTER,
) -> ThemeLotteryPhase:
    """Return the phase policy and the next review time.

    The branch is decided by the tickets' ``aggregate_retainability``, never by
    the (possibly whitelisted) goal kind: a retainable ticket that uses an
    explicit ``exhaust_all`` goal is still retainable and must not inherit the
    non-retainable tail.  A non-retainable remainder is deferred until the
    final day tail time; after that boundary the existing single-draw remainder
    handling applies.  A ``now`` later than ``final_day`` is an explicit
    non-executable ``expired`` state, never a past ``next_time``.
    """

    if resolution.status != "ready" or resolution.policy is None:
        return ThemeLotteryPhase(
            status="pending_evidence",
            reason=resolution.reason,
            missing_evidence=resolution.missing_evidence,
        )
    if now.tzinfo is None:
        raise ValueError("主题集抽奖时钟必须带时区")
    if now.date() > final_day:
        return ThemeLotteryPhase(
            status="expired",
            reason="活动期已结束，抽奖阶段不再执行",
            next_time=None,
        )
    policy = resolution.policy
    if resolution.aggregate_retainability != "non_retainable":
        return ThemeLotteryPhase(
            status="ready",
            reason="可留存券按自身目标判定，不套用不可留存尾批",
            policy=policy,
            remainder_mode=policy.remainder_mode,
        )
    tail_at = theme_lottery_tail_at(
        final_day=final_day,
        now=now,
        final_day_tail_after=final_day_tail_after,
    )
    after_tail = (
        now.date() == final_day
        and now.timetz().replace(tzinfo=None) >= final_day_tail_after
    )
    remainder_mode: LotteryRemainderMode = "single" if after_tail else "defer"
    return ThemeLotteryPhase(
        status="ready",
        reason="不可留存券尾日前 defer，尾日后 single",
        policy=replace(policy, remainder_mode=remainder_mode),
        remainder_mode=remainder_mode,
        next_time=None if after_tail else tail_at,
    )


__all__ = [
    "ThemeLotteryPhase",
    "ThemeLotteryPolicyResolution",
    "ThemeLotteryTicket",
    "TicketRetainability",
    "resolve_theme_lottery_phase",
    "resolve_theme_lottery_policy",
    "theme_lottery_tail_at",
]
