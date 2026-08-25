"""Activity-agnostic exchange target planning.

Activities own their Runtime collectors and persistence models.  This module only
consumes normalized exchange-currency measurements and answers the shared
business question: how many more challenges are needed for a target?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ExchangeMeasurement:
    """One cumulative exchange-currency observation.

    ``attempt_count_delta`` is the number of activity-specific executions since
    the previous observation.  A baseline therefore normally uses ``0`` or
    ``None``.  An activity may present an attempt as a challenge, exploration,
    battle, or another user-facing action.
    """

    exchange_currency: int
    attempt_count_delta: int | None = None


@dataclass(frozen=True, slots=True)
class ExchangeYieldRate:
    """An exact batch yield kept as an integer ratio."""

    exchange_currency_delta: int
    attempt_count: int

    @property
    def exchange_currency_per_attempt(self) -> float:
        return self.exchange_currency_delta / self.attempt_count


@dataclass(frozen=True, slots=True)
class ExchangeTargetBudget:
    """One-shot attempt budget for an ideal target and its safe fallback."""

    ideal_target_currency: int
    fallback_target_currency: int
    available_attempts: int
    ideal_attempts: int | None
    fallback_attempts: int | None
    planned_attempts: int
    target_level: str
    shortfall_attempts: int | None


@dataclass(frozen=True, slots=True)
class ExchangeCurrencyGap:
    """Fresh-currency requirement from both acquisition and spend ledgers."""

    target_total_tokens: int
    target_remaining_tokens: int
    current_currency: int
    cumulative_currency: int
    balance_gap: int
    cumulative_gap: int
    required_new_currency: int


def calculate_exchange_currency_gap(
    *,
    target_total_tokens: int,
    target_remaining_tokens: int,
    current_currency: int,
    cumulative_currency: int,
) -> ExchangeCurrencyGap:
    """Protect against both already-purchased rows and accidental overspend.

    ``target_remaining_tokens`` comes from current ``purchased_count`` values.
    The balance gap catches currency spent outside the target; the cumulative
    gap prevents treating a temporarily large balance as earned target progress.
    The larger gap is the only safe amount to acquire next.
    """

    balance_gap = max(0, int(target_remaining_tokens) - int(current_currency))
    cumulative_gap = max(
        0,
        int(target_total_tokens) - int(cumulative_currency),
    )
    return ExchangeCurrencyGap(
        target_total_tokens=int(target_total_tokens),
        target_remaining_tokens=int(target_remaining_tokens),
        current_currency=int(current_currency),
        cumulative_currency=int(cumulative_currency),
        balance_gap=balance_gap,
        cumulative_gap=cumulative_gap,
        required_new_currency=max(balance_gap, cumulative_gap),
    )


def latest_exchange_yield_rate(
    measurements: Iterable[ExchangeMeasurement],
) -> ExchangeYieldRate | None:
    """Select the latest adjacent pair that forms a positive yield sample."""

    previous: ExchangeMeasurement | None = None
    latest: ExchangeYieldRate | None = None
    for current in measurements:
        if previous is not None:
            attempt_count = int(current.attempt_count_delta or 0)
            exchange_currency_delta = (
                int(current.exchange_currency) - int(previous.exchange_currency)
            )
            if attempt_count > 0 and exchange_currency_delta > 0:
                latest = ExchangeYieldRate(
                    exchange_currency_delta=exchange_currency_delta,
                    attempt_count=attempt_count,
                )
        previous = current
    return latest


def estimate_remaining_attempts(
    *,
    accumulated_exchange_currency: int,
    target_exchange_currency: int | None,
    yield_rate: ExchangeYieldRate | None,
) -> int | None:
    """Estimate executions for one cumulative target using exact ceiling math.

    Reached targets, target-less rows such as unlimited shop items, and missing
    yield samples intentionally return ``None`` so presentation layers can stay
    blank instead of displaying a misleading zero.
    """

    if target_exchange_currency is None or yield_rate is None:
        return None
    remaining_exchange_currency = (
        int(target_exchange_currency) - int(accumulated_exchange_currency)
    )
    if remaining_exchange_currency <= 0:
        return None
    return (
        remaining_exchange_currency * yield_rate.attempt_count
        + yield_rate.exchange_currency_delta
        - 1
    ) // yield_rate.exchange_currency_delta


def plan_available_attempts(
    *,
    accumulated_exchange_currency: int,
    ideal_target_currency: int,
    fallback_target_currency: int,
    available_attempts: int,
    yield_rate: ExchangeYieldRate | None,
) -> ExchangeTargetBudget:
    """Choose one continuous batch without periodically re-planning it.

    Complete the ideal target when the verified inventory covers it. Otherwise
    consume at most the verified inventory and report whether the fallback is
    reachable. Missing yield evidence fails closed with a zero-sized batch.
    """

    available = max(0, int(available_attempts))
    ideal = estimate_remaining_attempts(
        accumulated_exchange_currency=accumulated_exchange_currency,
        target_exchange_currency=ideal_target_currency,
        yield_rate=yield_rate,
    )
    fallback = estimate_remaining_attempts(
        accumulated_exchange_currency=accumulated_exchange_currency,
        target_exchange_currency=fallback_target_currency,
        yield_rate=yield_rate,
    )
    if yield_rate is None:
        return ExchangeTargetBudget(
            ideal_target_currency=int(ideal_target_currency),
            fallback_target_currency=int(fallback_target_currency),
            available_attempts=available,
            ideal_attempts=None,
            fallback_attempts=None,
            planned_attempts=0,
            target_level="unmeasured",
            shortfall_attempts=None,
        )
    ideal_needed = int(ideal or 0)
    fallback_needed = int(fallback or 0)
    if ideal_needed <= available:
        target_level = "stage9"
        planned = ideal_needed
    elif fallback_needed <= available:
        target_level = "stage8"
        planned = available
    else:
        target_level = "approach_stage8"
        planned = available
    return ExchangeTargetBudget(
        ideal_target_currency=int(ideal_target_currency),
        fallback_target_currency=int(fallback_target_currency),
        available_attempts=available,
        ideal_attempts=ideal,
        fallback_attempts=fallback,
        planned_attempts=planned,
        target_level=target_level,
        shortfall_attempts=max(0, ideal_needed - available),
    )
