"""Activity-agnostic exchange target planning.

Activities own their Runtime collectors and persistence models.  This module only
consumes normalized exchange-currency measurements and answers the shared
business question: how many more challenges are needed for a target?
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


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
class ExchangeYieldFeatureSpec:
    """Activity-owned definition of one consumable yield feature."""

    key: str
    nominal_extra_multiplier: float
    default_realization_ratio: float = 0.5
    prior_item_weight: float = 50.0


@dataclass(frozen=True, slots=True)
class ExchangeYieldFeatureFit:
    key: str
    nominal_extra_multiplier: float
    realization_ratio: float
    effective_multiplier: float


@dataclass(frozen=True, slots=True)
class ExchangeYieldScatterSample:
    """One activity-agnostic batch point and its observed feature usage."""

    exchange_currency_delta: int
    attempt_count: int
    feature_item_usage: tuple[tuple[str, int], ...] = ()

    def usage(self, key: str) -> int:
        return sum(int(count) for name, count in self.feature_item_usage if name == key)


@dataclass(frozen=True, slots=True)
class ExchangeYieldScatterModel:
    """Attempt-weighted fit with activity-defined, shrinkable yield features."""

    plain_currency_per_attempt: float
    feature_fits: tuple[ExchangeYieldFeatureFit, ...]
    sample_count: int
    total_attempts: int
    plain_attempts: int
    fit_rmse_per_attempt: float

    def currency_per_attempt(
        self,
        *,
        feature_item_fractions: Mapping[str, float] | None = None,
    ) -> float:
        fractions = feature_item_fractions or {}
        factor = 1.0
        for feature in self.feature_fits:
            raw_fraction = float(fractions.get(feature.key, 0.0))
            if not math.isfinite(raw_fraction):
                raise ValueError(f"增益特征占比不是有限数: {feature.key}")
            fraction = min(1.0, max(0.0, raw_fraction))
            factor += (
                feature.nominal_extra_multiplier
                * feature.realization_ratio
                * fraction
            )
        return self.plain_currency_per_attempt * factor

    def estimate_attempts(
        self,
        required_currency: int,
        *,
        feature_item_fractions: Mapping[str, float] | None = None,
    ) -> int:
        gap = max(0, int(required_currency))
        if gap == 0:
            return 0
        rate = self.currency_per_attempt(
            feature_item_fractions=feature_item_fractions,
        )
        return math.ceil(gap / rate)


def _effective_attempts(
    row: ExchangeYieldScatterSample,
    specs: tuple[ExchangeYieldFeatureSpec, ...],
    realizations: Mapping[str, float],
) -> float:
    value = float(row.attempt_count)
    for spec in specs:
        value += (
            spec.nominal_extra_multiplier
            * realizations[spec.key]
            * row.usage(spec.key)
        )
    return value


def _refit_plain_scatter_rate(
    rows: list[ExchangeYieldScatterSample],
    specs: tuple[ExchangeYieldFeatureSpec, ...],
    realizations: Mapping[str, float],
) -> float:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        attempts = float(row.attempt_count)
        effective_attempts = _effective_attempts(row, specs, realizations)
        # Batch variance grows roughly with its size.  Weighting a batch by
        # 1/n therefore gives every underlying attempt comparable influence.
        weight = 1.0 / attempts
        numerator += weight * effective_attempts * row.exchange_currency_delta
        denominator += weight * effective_attempts * effective_attempts
    if denominator <= 0.0:
        raise ValueError("兑币增益散点缺少有效拟合分母")
    return numerator / denominator


def _fit_feature_realizations(
    rows: list[ExchangeYieldScatterSample],
    specs: tuple[ExchangeYieldFeatureSpec, ...],
    *,
    plain_rate: float,
    initial: Mapping[str, float],
) -> dict[str, float]:
    """Fit normalized bonus factors while keeping priors activity-independent.

    The objective is expressed as currency-per-attempt divided by the plain
    rate.  This matters because a nominal ``4x`` feature is represented as an
    extra multiplier of ``3``: its prior must constrain the realization ratio,
    not accidentally become nine times stronger merely because ``3²`` appears
    in an unnormalised currency regression.
    """

    realizations = dict(initial)
    for _iteration in range(64):
        previous = tuple(realizations[spec.key] for spec in specs)
        for spec in specs:
            prior = min(1.0, max(0.0, float(spec.default_realization_ratio)))
            numerator = spec.prior_item_weight * prior
            denominator = spec.prior_item_weight
            for row in rows:
                usage_fraction = row.usage(spec.key) / row.attempt_count
                axis = spec.nominal_extra_multiplier * usage_fraction
                if axis <= 0.0:
                    continue
                observed_extra = (
                    row.exchange_currency_delta
                    / (plain_rate * row.attempt_count)
                    - 1.0
                )
                other_extra = 0.0
                for other in specs:
                    if other.key == spec.key:
                        continue
                    other_extra += (
                        other.nominal_extra_multiplier
                        * realizations[other.key]
                        * row.usage(other.key)
                        / row.attempt_count
                    )
                # A batch is an aggregate of its attempts.  Weighting its
                # per-attempt residual by n lets a 1,000-run point carry about
                # 1,000 times the evidence of a single-run point.
                weight = float(row.attempt_count)
                numerator += weight * axis * (observed_extra - other_extra)
                denominator += weight * axis * axis
            if denominator > 0.0:
                realizations[spec.key] = min(
                    1.0,
                    max(0.0, numerator / denominator),
                )
        current = tuple(realizations[spec.key] for spec in specs)
        if max((abs(a - b) for a, b in zip(previous, current)), default=0.0) < 1e-9:
            break
    return realizations


def fit_exchange_yield_scatter_model(
    samples: Iterable[ExchangeYieldScatterSample],
    *,
    feature_specs: Iterable[ExchangeYieldFeatureSpec] = (),
) -> ExchangeYieldScatterModel | None:
    """Fit the common gameplay-ranking scatter base.

    Activities supply feature names and advertised extra multipliers.  The
    base learns each feature's realized 0..1 share independently with a small
    prior, so large real batches dominate without assuming nominal bonuses are
    fully paid out.
    """

    specs = tuple(feature_specs)
    if len({spec.key for spec in specs}) != len(specs) or any(
        not spec.key
        or not math.isfinite(spec.nominal_extra_multiplier)
        or spec.nominal_extra_multiplier <= 0.0
        or not math.isfinite(spec.default_realization_ratio)
        or not 0.0 <= spec.default_realization_ratio <= 1.0
        or not math.isfinite(spec.prior_item_weight)
        or spec.prior_item_weight < 0.0
        for spec in specs
    ):
        raise ValueError("兑币散点增益特征规格无效或重复")
    allowed_keys = {spec.key for spec in specs}
    rows: list[ExchangeYieldScatterSample] = []
    for row in samples:
        usage_keys = [key for key, _count in row.feature_item_usage]
        valid_usage = (
            len(usage_keys) == len(set(usage_keys))
            and all(
                key in allowed_keys
                and isinstance(count, int)
                and 0 <= count <= row.attempt_count
                for key, count in row.feature_item_usage
            )
        )
        if row.attempt_count > 0 and row.exchange_currency_delta > 0 and valid_usage:
            rows.append(row)
    if not rows:
        return None

    realizations: dict[str, float] = {
        spec.key: min(1.0, max(0.0, float(spec.default_realization_ratio)))
        for spec in specs
    }
    plain_rows = [
        row
        for row in rows
        if all(row.usage(spec.key) == 0 for spec in specs)
    ]
    if plain_rows:
        # A boosted point cannot identify the baseline and bonus separately.
        # Once direct plain observations exist, they alone anchor the baseline;
        # large boosted batches instead contribute their full weight to fitting
        # feature realization.  This prevents a nominal bonus assumption from
        # contaminating the no-item forecast.
        plain_rate = sum(row.exchange_currency_delta for row in plain_rows) / sum(
            row.attempt_count for row in plain_rows
        )
        realizations = _fit_feature_realizations(
            rows,
            specs,
            plain_rate=plain_rate,
            initial=realizations,
        )
    else:
        # With no plain point the decomposition is underidentified.  Retain the
        # activity priors and solve only the common scale; callers can inspect
        # ``plain_attempts == 0`` and treat the forecast as lower confidence.
        plain_rate = _refit_plain_scatter_rate(rows, specs, realizations)

    squared_error = 0.0
    total_attempts = 0
    plain_attempts = 0
    for row in rows:
        predicted = plain_rate * _effective_attempts(row, specs, realizations)
        error_per_attempt = (
            row.exchange_currency_delta - predicted
        ) / row.attempt_count
        squared_error += row.attempt_count * error_per_attempt * error_per_attempt
        total_attempts += row.attempt_count
        if all(row.usage(spec.key) == 0 for spec in specs):
            plain_attempts += row.attempt_count

    return ExchangeYieldScatterModel(
        plain_currency_per_attempt=plain_rate,
        feature_fits=tuple(
            ExchangeYieldFeatureFit(
                key=spec.key,
                nominal_extra_multiplier=spec.nominal_extra_multiplier,
                realization_ratio=realizations[spec.key],
                effective_multiplier=(
                    1.0
                    + spec.nominal_extra_multiplier * realizations[spec.key]
                ),
            )
            for spec in specs
        ),
        sample_count=len(rows),
        total_attempts=total_attempts,
        plain_attempts=plain_attempts,
        fit_rmse_per_attempt=math.sqrt(squared_error / total_attempts),
    )


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
        target_level = "收尾道具"
        planned = ideal_needed
    elif fallback_needed <= available:
        target_level = "其他折扣"
        planned = available
    else:
        target_level = "尽量接近其他折扣"
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
