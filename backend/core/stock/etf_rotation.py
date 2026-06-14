from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .market_data import MARKET_DATA_PROVIDER_AKSHARE, connect_market_data_db


@dataclass(frozen=True)
class EtfRotationAsset:
    market: str
    symbol: str
    name: str
    role: str


@dataclass(frozen=True)
class EtfRotationHolding:
    market: str
    symbol: str
    name: str
    weight: float
    fast_momentum: float
    forward_return: float | None = None


@dataclass(frozen=True)
class EtfRotationPeriod:
    date: str
    year: str
    cash_fraction: float
    return_value: float
    holdings: tuple[EtfRotationHolding, ...]


@dataclass(frozen=True)
class EtfRotationBacktestResult:
    strategy_id: str
    source: str
    parameters: dict[str, Any]
    periods: tuple[EtfRotationPeriod, ...]
    annual_returns: dict[str, float]
    total_return: float
    latest_signal: EtfRotationPeriod | None


@dataclass(frozen=True)
class EtfPremiumExecutionGuardItem:
    market: str
    symbol: str
    name: str
    weight: float
    discount_rate_percent: float | None
    premium_rate_percent: float | None
    action: str
    reason: str


ETF_ROTATION_STRATEGY_ID = "cross_asset_etf_weekly_relative_momentum_abs_filter"
ETF_HAA_STRATEGY_ID = "cross_asset_etf_hybrid_asset_allocation_strict_canary"
ETF_TOM_STRATEGY_ID = "nasdaq_etf_turn_of_month_close_to_close"
ETF_INVERSE_VARIANCE_STRATEGY_ID = "cross_asset_etf_inverse_variance_defensive_sleeve"
ETF_DUAL_MOMENTUM_SWITCH_STRATEGY_ID = "cross_asset_etf_dual_momentum_top1_defensive_switch"
ETF_BREADTH_FILTERED_CANARY_STRATEGY_ID = "cross_asset_etf_breadth_filtered_canary_rotation"

DEFAULT_ETF_ROTATION_POOL: tuple[EtfRotationAsset, ...] = (
    EtfRotationAsset("SH", "510300", "沪深300ETF", "offensive"),
    EtfRotationAsset("SH", "510500", "中证500ETF", "offensive"),
    EtfRotationAsset("SH", "588000", "科创50ETF", "offensive"),
    EtfRotationAsset("SZ", "159915", "创业板ETF", "offensive"),
    EtfRotationAsset("SH", "513520", "日经ETF", "offensive"),
    EtfRotationAsset("SH", "513500", "标普500ETF", "offensive"),
    EtfRotationAsset("SH", "513100", "纳指ETF", "offensive"),
    EtfRotationAsset("SH", "513180", "恒生科技ETF", "offensive"),
    EtfRotationAsset("SH", "515220", "煤炭ETF", "offensive"),
    EtfRotationAsset("SH", "512400", "有色ETF", "offensive"),
    EtfRotationAsset("SH", "512800", "银行ETF", "offensive"),
    EtfRotationAsset("SH", "518880", "黄金ETF", "defensive"),
    EtfRotationAsset("SH", "511260", "十年国债ETF", "defensive"),
    EtfRotationAsset("SH", "511010", "国债ETF", "defensive"),
)

DEFAULT_CANARY_SYMBOLS = frozenset({"510300", "513100", "159915"})
DEFAULT_DUAL_MOMENTUM_OFFENSIVE_SYMBOLS = frozenset({
    "510300",
    "510500",
    "588000",
    "159915",
    "513100",
    "513500",
    "513520",
    "513180",
})
DEFAULT_DUAL_MOMENTUM_DEFENSIVE_SYMBOLS = frozenset({"518880", "511260", "511010"})


def compute_cross_asset_etf_canary_rotation(
    *,
    start_date: str = "2021-01-01",
    hold_days: int = 10,
    top_n: int = 1,
    cost: float = 0.001,
    canary_threshold: float = 0.5,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute the fixed canary ETF rotation candidate from persisted daily ETF bars."""
    bars = _load_etf_daily_bars(pool)
    periods = _compute_rotation_periods(
        bars=bars,
        pool=pool,
        start_date=start_date,
        hold_days=hold_days,
        top_n=top_n,
        cost=cost,
        canary_threshold=canary_threshold,
    )
    annual_returns = _compound_by_year(periods)
    total_return = _compound(period.return_value for period in periods)
    latest_signal = _compute_latest_signal(
        bars=bars,
        pool=pool,
        start_date=start_date,
        top_n=top_n,
        canary_threshold=canary_threshold,
    )
    return EtfRotationBacktestResult(
        strategy_id=ETF_ROTATION_STRATEGY_ID,
        source="cache:market_kline:cross_asset_etf_canary_rotation",
        parameters={
            "start_date": start_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "cost": cost,
            "canary_threshold": canary_threshold,
            "canary_symbols": sorted(DEFAULT_CANARY_SYMBOLS),
        },
        periods=tuple(periods),
        annual_returns=annual_returns,
        total_return=total_return,
        latest_signal=latest_signal,
    )


def compute_cross_asset_etf_hybrid_asset_allocation(
    *,
    start_date: str = "2021-01-01",
    hold_days: int = 10,
    top_n: int = 1,
    cost: float = 0.001,
    canary_symbol: str = "510300",
    momentum_threshold: float = 1.0,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute a strict HAA-style ETF candidate from persisted daily ETF bars.

    This is a research candidate, not an execution recommendation. The strict
    momentum threshold is intentionally explicit because it is the main
    overfitting risk in this variant.
    """
    bars = _load_etf_daily_bars(pool)
    periods = _compute_hybrid_asset_allocation_periods(
        bars=bars,
        pool=pool,
        start_date=start_date,
        hold_days=hold_days,
        top_n=top_n,
        cost=cost,
        canary_symbol=canary_symbol,
        momentum_threshold=momentum_threshold,
    )
    annual_returns = _compound_by_year(periods)
    total_return = _compound(period.return_value for period in periods)
    latest_signal = _compute_hybrid_asset_allocation_latest_signal(
        bars=bars,
        pool=pool,
        start_date=start_date,
        top_n=top_n,
        canary_symbol=canary_symbol,
        momentum_threshold=momentum_threshold,
    )
    return EtfRotationBacktestResult(
        strategy_id=ETF_HAA_STRATEGY_ID,
        source="cache:market_kline:cross_asset_etf_hybrid_asset_allocation",
        parameters={
            "start_date": start_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "cost": cost,
            "canary_symbol": canary_symbol,
            "momentum_threshold": momentum_threshold,
        },
        periods=tuple(periods),
        annual_returns=annual_returns,
        total_return=total_return,
        latest_signal=latest_signal,
    )


def apply_etf_rotation_volatility_target_overlay(
    result: EtfRotationBacktestResult,
    *,
    lookback_periods: int = 3,
    target_annual_volatility: float = 0.08,
    min_exposure: float = 0.0,
    max_exposure: float = 1.0,
) -> EtfRotationBacktestResult:
    """Scale an ETF rotation result by recent realized period volatility.

    The overlay only uses returns known before each rebalance. It is deliberately
    long-only and unlevered by default: exposure can be reduced, not increased.
    """
    scaled_periods, latest_exposure = _apply_volatility_target_to_periods(
        result.periods,
        lookback_periods=lookback_periods,
        target_annual_volatility=target_annual_volatility,
        min_exposure=min_exposure,
        max_exposure=max_exposure,
    )
    latest_signal = (
        _scale_period_exposure(result.latest_signal, latest_exposure)
        if result.latest_signal is not None
        else None
    )
    return EtfRotationBacktestResult(
        strategy_id=f"{result.strategy_id}_vol_target_overlay",
        source=f"{result.source}:vol_target_overlay",
        parameters={
            **result.parameters,
            "base_strategy_id": result.strategy_id,
            "volatility_target": {
                "lookback_periods": lookback_periods,
                "target_annual_volatility": target_annual_volatility,
                "min_exposure": min_exposure,
                "max_exposure": max_exposure,
            },
        },
        periods=tuple(scaled_periods),
        annual_returns=_compound_by_year(scaled_periods),
        total_return=_compound(period.return_value for period in scaled_periods),
        latest_signal=latest_signal,
    )


def compute_nasdaq_etf_turn_of_month_strategy(
    *,
    start_date: str = "2021-01-01",
    symbol: str = "513100",
    pre_month_end_days: int = 1,
    post_month_start_days: int = 0,
    cost: float = 0.0005,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute a cost-sensitive turn-of-month sleeve for a single ETF.

    The default rule buys the ETF at month-end close and exits at the first
    trading day's close of the next month. It is a research sleeve, not a broad
    market timing signal.
    """
    asset_by_symbol = {asset.symbol: asset for asset in pool}
    if symbol not in asset_by_symbol:
        raise ValueError(f"ETF symbol is not in the fixed rotation pool: {symbol}")
    bars = _load_etf_daily_bars(pool)
    periods = _compute_turn_of_month_periods(
        bars=bars,
        asset=asset_by_symbol[symbol],
        start_date=start_date,
        pre_month_end_days=pre_month_end_days,
        post_month_start_days=post_month_start_days,
        cost=cost,
    )
    annual_returns = _compound_by_year(periods)
    total_return = _compound(period.return_value for period in periods)
    return EtfRotationBacktestResult(
        strategy_id=ETF_TOM_STRATEGY_ID,
        source="cache:market_kline:nasdaq_etf_turn_of_month",
        parameters={
            "start_date": start_date,
            "symbol": symbol,
            "pre_month_end_days": pre_month_end_days,
            "post_month_start_days": post_month_start_days,
            "cost": cost,
            "execution": "close_to_close",
        },
        periods=tuple(periods),
        annual_returns=annual_returns,
        total_return=total_return,
        latest_signal=periods[-1] if periods else None,
    )


def compute_cross_asset_etf_inverse_variance_sleeve(
    *,
    start_date: str = "2021-01-01",
    lookback_days: int = 21,
    hold_days: int = 21,
    cost: float = 0.0005,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute a low-volatility inverse-variance ETF sleeve.

    This deliberately behaves like a defensive cash/bond substitute. It uses
    only backward-looking realized variance and does not forecast returns.
    """
    bars = _load_etf_daily_bars(pool)
    periods = _compute_inverse_variance_periods(
        bars=bars,
        pool=pool,
        start_date=start_date,
        lookback_days=lookback_days,
        hold_days=hold_days,
        cost=cost,
    )
    latest_signal = _compute_inverse_variance_latest_signal(
        bars=bars,
        pool=pool,
        start_date=start_date,
        lookback_days=lookback_days,
    )
    return EtfRotationBacktestResult(
        strategy_id=ETF_INVERSE_VARIANCE_STRATEGY_ID,
        source="cache:market_kline:cross_asset_etf_inverse_variance_sleeve",
        parameters={
            "start_date": start_date,
            "lookback_days": lookback_days,
            "hold_days": hold_days,
            "cost": cost,
            "weighting": "inverse_realized_variance",
        },
        periods=tuple(periods),
        annual_returns=_compound_by_year(periods),
        total_return=_compound(period.return_value for period in periods),
        latest_signal=latest_signal,
    )


def compute_cross_asset_etf_dual_momentum_switch(
    *,
    start_date: str = "2021-01-01",
    hold_days: int = 10,
    top_n: int = 1,
    cost: float = 0.001,
    momentum_threshold: float = 0.0,
    offensive_symbols: frozenset[str] = DEFAULT_DUAL_MOMENTUM_OFFENSIVE_SYMBOLS,
    defensive_symbols: frozenset[str] = DEFAULT_DUAL_MOMENTUM_DEFENSIVE_SYMBOLS,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute a simple dual-momentum ETF switch.

    The rule is intentionally close to classic GEM / sector momentum templates:
    use relative momentum inside the offensive universe when absolute momentum
    is positive, otherwise switch fully to the strongest defensive asset.
    """
    bars = _load_etf_daily_bars(pool)
    periods = _compute_dual_momentum_switch_periods(
        bars=bars,
        pool=pool,
        start_date=start_date,
        hold_days=hold_days,
        top_n=top_n,
        cost=cost,
        momentum_threshold=momentum_threshold,
        offensive_symbols=offensive_symbols,
        defensive_symbols=defensive_symbols,
    )
    latest_signal = _compute_dual_momentum_switch_latest_signal(
        bars=bars,
        pool=pool,
        start_date=start_date,
        top_n=top_n,
        momentum_threshold=momentum_threshold,
        offensive_symbols=offensive_symbols,
        defensive_symbols=defensive_symbols,
    )
    return EtfRotationBacktestResult(
        strategy_id=ETF_DUAL_MOMENTUM_SWITCH_STRATEGY_ID,
        source="cache:market_kline:cross_asset_etf_dual_momentum_switch",
        parameters={
            "start_date": start_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "cost": cost,
            "momentum_threshold": momentum_threshold,
            "offensive_symbols": sorted(offensive_symbols),
            "defensive_symbols": sorted(defensive_symbols),
        },
        periods=tuple(periods),
        annual_returns=_compound_by_year(periods),
        total_return=_compound(period.return_value for period in periods),
        latest_signal=latest_signal,
    )


def compute_cross_asset_etf_breadth_filtered_canary_rotation(
    *,
    start_date: str = "2021-01-01",
    hold_days: int = 10,
    top_n: int = 1,
    cost: float = 0.001,
    canary_threshold: float = 0.5,
    breadth_ma_days: int = 120,
    min_breadth_ratio: float = 0.7,
    pool: tuple[EtfRotationAsset, ...] = DEFAULT_ETF_ROTATION_POOL,
) -> EtfRotationBacktestResult:
    """Compute canary ETF rotation with an offensive breadth participation filter."""
    bars = _load_etf_daily_bars(pool)
    periods = _compute_breadth_filtered_canary_periods(
        bars=bars,
        pool=pool,
        start_date=start_date,
        hold_days=hold_days,
        top_n=top_n,
        cost=cost,
        canary_threshold=canary_threshold,
        breadth_ma_days=breadth_ma_days,
        min_breadth_ratio=min_breadth_ratio,
    )
    latest_signal = _compute_breadth_filtered_canary_latest_signal(
        bars=bars,
        pool=pool,
        start_date=start_date,
        top_n=top_n,
        canary_threshold=canary_threshold,
        breadth_ma_days=breadth_ma_days,
        min_breadth_ratio=min_breadth_ratio,
    )
    return EtfRotationBacktestResult(
        strategy_id=ETF_BREADTH_FILTERED_CANARY_STRATEGY_ID,
        source="cache:market_kline:cross_asset_etf_breadth_filtered_canary_rotation",
        parameters={
            "start_date": start_date,
            "hold_days": hold_days,
            "top_n": top_n,
            "cost": cost,
            "canary_threshold": canary_threshold,
            "breadth_ma_days": breadth_ma_days,
            "min_breadth_ratio": min_breadth_ratio,
            "breadth_universe": "offensive_etf_pool",
        },
        periods=tuple(periods),
        annual_returns=_compound_by_year(periods),
        total_return=_compound(period.return_value for period in periods),
        latest_signal=latest_signal,
    )


def evaluate_etf_premium_execution_guard(
    holdings: tuple[EtfRotationHolding, ...],
    *,
    discount_rate_by_symbol: dict[str, float | None],
    max_premium_percent: float = 2.0,
) -> tuple[EtfPremiumExecutionGuardItem, ...]:
    """Evaluate whether ETF orders should be blocked by premium/discount data.

    AKShare's discount rate is positive for discount and negative for premium.
    The guard only blocks excessive premium on buys; discount or small premium
    is allowed, while missing data is surfaced for manual review.
    """
    items: list[EtfPremiumExecutionGuardItem] = []
    normalized_max_premium = max(0.0, float(max_premium_percent))
    for holding in holdings:
        discount_rate = discount_rate_by_symbol.get(holding.symbol)
        if discount_rate is None:
            items.append(
                EtfPremiumExecutionGuardItem(
                    market=holding.market,
                    symbol=holding.symbol,
                    name=holding.name,
                    weight=holding.weight,
                    discount_rate_percent=None,
                    premium_rate_percent=None,
                    action="review_missing_premium",
                    reason="缺少ETF折溢价数据，需人工确认后再买入",
                )
            )
            continue
        premium_rate = max(0.0, -float(discount_rate))
        if premium_rate > normalized_max_premium:
            items.append(
                EtfPremiumExecutionGuardItem(
                    market=holding.market,
                    symbol=holding.symbol,
                    name=holding.name,
                    weight=holding.weight,
                    discount_rate_percent=float(discount_rate),
                    premium_rate_percent=premium_rate,
                    action="block_high_premium",
                    reason=f"ETF溢价 {premium_rate:.2f}% 高于阈值 {normalized_max_premium:.2f}%",
                )
            )
        else:
            items.append(
                EtfPremiumExecutionGuardItem(
                    market=holding.market,
                    symbol=holding.symbol,
                    name=holding.name,
                    weight=holding.weight,
                    discount_rate_percent=float(discount_rate),
                    premium_rate_percent=premium_rate,
                    action="allow",
                    reason="ETF折溢价在允许范围内",
                )
            )
    return tuple(items)


def serialize_etf_rotation_backtest_result(result: EtfRotationBacktestResult) -> dict[str, Any]:
    return {
        "strategy_id": result.strategy_id,
        "source": result.source,
        "parameters": result.parameters,
        "annual_returns": result.annual_returns,
        "total_return": result.total_return,
        "latest_signal": _period_to_dict(result.latest_signal) if result.latest_signal else None,
        "period_count": len(result.periods),
        "periods": [_period_to_dict(period) for period in result.periods],
    }


def _load_etf_daily_bars(pool: tuple[EtfRotationAsset, ...]) -> dict[str, list[dict[str, Any]]]:
    with connect_market_data_db() as conn:
        data: dict[str, list[dict[str, Any]]] = {}
        for asset in pool:
            rows = conn.execute(
                """
                SELECT time_key, close
                FROM market_kline
                WHERE provider = ?
                  AND market = ?
                  AND symbol = ?
                  AND ktype = 'daily'
                ORDER BY time_key
                """,
                (MARKET_DATA_PROVIDER_AKSHARE, asset.market, asset.symbol),
            ).fetchall()
            data[asset.symbol] = [dict(row) for row in rows]
    return data


def _compute_hybrid_asset_allocation_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    hold_days: int,
    top_n: int,
    cost: float,
    canary_symbol: str,
    momentum_threshold: float,
) -> list[EtfRotationPeriod]:
    indexes = _build_indexes(bars)
    periods: list[EtfRotationPeriod] = []
    previous_symbols: set[str] = set()
    for date in _month_end_dates(bars, indexes, start_date=start_date):
        features = _features_for_date(bars, indexes, pool, date, hold_days=hold_days)
        if not features:
            continue
        holdings = _select_hybrid_asset_allocation_holdings(
            features,
            canary_symbol=canary_symbol,
            momentum_threshold=momentum_threshold,
            top_n=top_n,
        )
        selected_symbols = {holding.symbol for holding in holdings}
        gross_return = sum((holding.forward_return or 0.0) * holding.weight for holding in holdings)
        turnover = len(selected_symbols.symmetric_difference(previous_symbols)) / max(1, len(selected_symbols) or top_n)
        return_value = gross_return - cost * turnover if holdings else 0.0
        periods.append(
            EtfRotationPeriod(
                date=date,
                year=date[:4],
                cash_fraction=_cash_fraction_from_holdings(holdings),
                return_value=return_value,
                holdings=holdings,
            )
        )
        previous_symbols = selected_symbols
    return periods


def _compute_hybrid_asset_allocation_latest_signal(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    top_n: int,
    canary_symbol: str,
    momentum_threshold: float,
) -> EtfRotationPeriod | None:
    indexes = _build_indexes(bars)
    dates = _available_dates(bars, start_date=start_date)
    if not dates:
        return None
    date = dates[-1]
    features = _features_for_date(bars, indexes, pool, date, hold_days=0)
    if not features:
        return None
    holdings = _select_hybrid_asset_allocation_holdings(
        features,
        canary_symbol=canary_symbol,
        momentum_threshold=momentum_threshold,
        top_n=top_n,
    )
    return EtfRotationPeriod(
        date=date,
        year=date[:4],
        cash_fraction=_cash_fraction_from_holdings(holdings),
        return_value=0.0,
        holdings=holdings,
    )


def _compute_rotation_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    hold_days: int,
    top_n: int,
    cost: float,
    canary_threshold: float,
) -> list[EtfRotationPeriod]:
    indexes = _build_indexes(bars)
    periods: list[EtfRotationPeriod] = []
    previous_symbols: set[str] = set()
    for date in _month_end_dates(bars, indexes, start_date=start_date):
        features = _features_for_date(bars, indexes, pool, date, hold_days=hold_days)
        if not features:
            continue
        cash_fraction = _cash_fraction(features, canary_threshold=canary_threshold)
        holdings = _select_holdings(features, cash_fraction=cash_fraction, top_n=top_n)
        selected_symbols = {holding.symbol for holding in holdings}
        gross_return = sum((holding.forward_return or 0.0) * holding.weight for holding in holdings)
        turnover = len(selected_symbols.symmetric_difference(previous_symbols)) / max(1, top_n)
        return_value = gross_return - cost * turnover if holdings else 0.0
        periods.append(
            EtfRotationPeriod(
                date=date,
                year=date[:4],
                cash_fraction=cash_fraction,
                return_value=return_value,
                holdings=holdings,
            )
        )
        previous_symbols = selected_symbols
    return periods


def _compute_latest_signal(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    top_n: int,
    canary_threshold: float,
) -> EtfRotationPeriod | None:
    indexes = _build_indexes(bars)
    dates = _available_dates(bars, start_date=start_date)
    if not dates:
        return None
    date = dates[-1]
    features = _features_for_date(bars, indexes, pool, date, hold_days=0)
    if not features:
        return None
    cash_fraction = _cash_fraction(features, canary_threshold=canary_threshold)
    holdings = _select_holdings(features, cash_fraction=cash_fraction, top_n=top_n)
    return EtfRotationPeriod(
        date=date,
        year=date[:4],
        cash_fraction=cash_fraction,
        return_value=0.0,
        holdings=holdings,
    )


def _apply_volatility_target_to_periods(
    periods: tuple[EtfRotationPeriod, ...],
    *,
    lookback_periods: int,
    target_annual_volatility: float,
    min_exposure: float,
    max_exposure: float,
) -> tuple[list[EtfRotationPeriod], float]:
    scaled_periods: list[EtfRotationPeriod] = []
    prior_returns: list[float] = []
    normalized_lookback = max(2, int(lookback_periods or 2))
    target_period_volatility = max(0.0, float(target_annual_volatility)) / math.sqrt(12)
    lower = max(0.0, float(min_exposure))
    upper = max(lower, float(max_exposure))
    latest_exposure = upper
    for period in periods:
        if len(prior_returns) >= normalized_lookback:
            realized_volatility = statistics.stdev(prior_returns[-normalized_lookback:])
            if realized_volatility > 1e-12:
                latest_exposure = min(upper, max(lower, target_period_volatility / realized_volatility))
            else:
                latest_exposure = upper
        else:
            latest_exposure = upper
        scaled_periods.append(_scale_period_exposure(period, latest_exposure))
        prior_returns.append(period.return_value)
    return scaled_periods, latest_exposure


def _compute_turn_of_month_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    asset: EtfRotationAsset,
    start_date: str,
    pre_month_end_days: int,
    post_month_start_days: int,
    cost: float,
) -> list[EtfRotationPeriod]:
    rows = bars.get(asset.symbol, [])
    if not rows:
        return []
    dates_by_month: dict[str, list[str]] = defaultdict(list)
    close_by_date: dict[str, float] = {}
    for row in rows:
        date = str(row.get("time_key") or "")
        if date < start_date:
            continue
        close = _float(row.get("close"))
        if not date or close <= 0:
            continue
        dates_by_month[date[:7]].append(date)
        close_by_date[date] = close
    periods: list[EtfRotationPeriod] = []
    months = sorted(dates_by_month)
    normalized_pre_days = max(1, int(pre_month_end_days or 1))
    normalized_post_days = max(0, int(post_month_start_days or 0))
    round_trip_cost = max(0.0, float(cost)) * 2
    for idx, month in enumerate(months[:-1]):
        current_dates = dates_by_month[month]
        next_dates = dates_by_month[months[idx + 1]]
        if len(current_dates) < normalized_pre_days or len(next_dates) <= normalized_post_days:
            continue
        buy_date = current_dates[-normalized_pre_days]
        sell_date = next_dates[normalized_post_days]
        buy_close = close_by_date.get(buy_date, 0.0)
        sell_close = close_by_date.get(sell_date, 0.0)
        if buy_close <= 0 or sell_close <= 0:
            continue
        forward_return = sell_close / buy_close - 1.0
        periods.append(
            EtfRotationPeriod(
                date=buy_date,
                year=buy_date[:4],
                cash_fraction=0.0,
                return_value=forward_return - round_trip_cost,
                holdings=(
                    EtfRotationHolding(
                        market=asset.market,
                        symbol=asset.symbol,
                        name=asset.name,
                        weight=1.0,
                        fast_momentum=0.0,
                        forward_return=forward_return,
                    ),
                ),
            )
        )
    return periods


def _compute_inverse_variance_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    lookback_days: int,
    hold_days: int,
    cost: float,
) -> list[EtfRotationPeriod]:
    indexes = _build_indexes(bars)
    periods: list[EtfRotationPeriod] = []
    previous_weights: dict[str, float] = {}
    for date in _month_end_dates(bars, indexes, start_date=start_date):
        holdings = _select_inverse_variance_holdings(
            bars=bars,
            indexes=indexes,
            pool=pool,
            date=date,
            lookback_days=lookback_days,
            hold_days=hold_days,
        )
        if not holdings:
            continue
        weights = {holding.symbol: holding.weight for holding in holdings}
        turnover = sum(
            abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
            for symbol in set(weights) | set(previous_weights)
        ) / 2
        gross_return = sum((holding.forward_return or 0.0) * holding.weight for holding in holdings)
        periods.append(
            EtfRotationPeriod(
                date=date,
                year=date[:4],
                cash_fraction=0.0,
                return_value=gross_return - max(0.0, cost) * turnover,
                holdings=holdings,
            )
        )
        previous_weights = weights
    return periods


def _compute_inverse_variance_latest_signal(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    lookback_days: int,
) -> EtfRotationPeriod | None:
    indexes = _build_indexes(bars)
    dates = _available_dates(bars, start_date=start_date)
    if not dates:
        return None
    date = dates[-1]
    holdings = _select_inverse_variance_holdings(
        bars=bars,
        indexes=indexes,
        pool=pool,
        date=date,
        lookback_days=lookback_days,
        hold_days=0,
    )
    if not holdings:
        return None
    return EtfRotationPeriod(
        date=date,
        year=date[:4],
        cash_fraction=0.0,
        return_value=0.0,
        holdings=holdings,
    )


def _compute_dual_momentum_switch_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    hold_days: int,
    top_n: int,
    cost: float,
    momentum_threshold: float,
    offensive_symbols: frozenset[str],
    defensive_symbols: frozenset[str],
) -> list[EtfRotationPeriod]:
    indexes = _build_indexes(bars)
    periods: list[EtfRotationPeriod] = []
    previous_weights: dict[str, float] = {}
    for date in _month_end_dates(bars, indexes, start_date=start_date):
        features = _features_for_date(bars, indexes, pool, date, hold_days=hold_days)
        holdings = _select_dual_momentum_switch_holdings(
            features,
            top_n=top_n,
            momentum_threshold=momentum_threshold,
            offensive_symbols=offensive_symbols,
            defensive_symbols=defensive_symbols,
        )
        if not holdings:
            continue
        weights = {holding.symbol: holding.weight for holding in holdings}
        turnover = sum(
            abs(weights.get(symbol, 0.0) - previous_weights.get(symbol, 0.0))
            for symbol in set(weights) | set(previous_weights)
        ) / 2
        gross_return = sum((holding.forward_return or 0.0) * holding.weight for holding in holdings)
        periods.append(
            EtfRotationPeriod(
                date=date,
                year=date[:4],
                cash_fraction=_cash_fraction_from_holdings(holdings),
                return_value=gross_return - max(0.0, cost) * turnover,
                holdings=holdings,
            )
        )
        previous_weights = weights
    return periods


def _compute_dual_momentum_switch_latest_signal(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    top_n: int,
    momentum_threshold: float,
    offensive_symbols: frozenset[str],
    defensive_symbols: frozenset[str],
) -> EtfRotationPeriod | None:
    indexes = _build_indexes(bars)
    dates = _available_dates(bars, start_date=start_date)
    if not dates:
        return None
    date = dates[-1]
    features = _features_for_date(bars, indexes, pool, date, hold_days=0)
    holdings = _select_dual_momentum_switch_holdings(
        features,
        top_n=top_n,
        momentum_threshold=momentum_threshold,
        offensive_symbols=offensive_symbols,
        defensive_symbols=defensive_symbols,
    )
    if not holdings:
        return None
    return EtfRotationPeriod(
        date=date,
        year=date[:4],
        cash_fraction=_cash_fraction_from_holdings(holdings),
        return_value=0.0,
        holdings=holdings,
    )


def _compute_breadth_filtered_canary_periods(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    hold_days: int,
    top_n: int,
    cost: float,
    canary_threshold: float,
    breadth_ma_days: int,
    min_breadth_ratio: float,
) -> list[EtfRotationPeriod]:
    indexes = _build_indexes(bars)
    periods: list[EtfRotationPeriod] = []
    previous_symbols: set[str] = set()
    for date in _month_end_dates(bars, indexes, start_date=start_date):
        features = _features_for_date(bars, indexes, pool, date, hold_days=hold_days)
        if not features:
            continue
        holdings = _select_breadth_filtered_canary_holdings(
            bars=bars,
            indexes=indexes,
            pool=pool,
            features=features,
            date=date,
            top_n=top_n,
            canary_threshold=canary_threshold,
            breadth_ma_days=breadth_ma_days,
            min_breadth_ratio=min_breadth_ratio,
        )
        if not holdings:
            continue
        selected_symbols = {holding.symbol for holding in holdings}
        gross_return = sum((holding.forward_return or 0.0) * holding.weight for holding in holdings)
        turnover = len(selected_symbols.symmetric_difference(previous_symbols)) / max(1, len(selected_symbols))
        periods.append(
            EtfRotationPeriod(
                date=date,
                year=date[:4],
                cash_fraction=_cash_fraction_from_holdings(holdings),
                return_value=gross_return - max(0.0, cost) * turnover,
                holdings=holdings,
            )
        )
        previous_symbols = selected_symbols
    return periods


def _compute_breadth_filtered_canary_latest_signal(
    *,
    bars: dict[str, list[dict[str, Any]]],
    pool: tuple[EtfRotationAsset, ...],
    start_date: str,
    top_n: int,
    canary_threshold: float,
    breadth_ma_days: int,
    min_breadth_ratio: float,
) -> EtfRotationPeriod | None:
    indexes = _build_indexes(bars)
    dates = _available_dates(bars, start_date=start_date)
    if not dates:
        return None
    date = dates[-1]
    features = _features_for_date(bars, indexes, pool, date, hold_days=0)
    if not features:
        return None
    holdings = _select_breadth_filtered_canary_holdings(
        bars=bars,
        indexes=indexes,
        pool=pool,
        features=features,
        date=date,
        top_n=top_n,
        canary_threshold=canary_threshold,
        breadth_ma_days=breadth_ma_days,
        min_breadth_ratio=min_breadth_ratio,
    )
    if not holdings:
        return None
    return EtfRotationPeriod(
        date=date,
        year=date[:4],
        cash_fraction=_cash_fraction_from_holdings(holdings),
        return_value=0.0,
        holdings=holdings,
    )


def _select_inverse_variance_holdings(
    *,
    bars: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    pool: tuple[EtfRotationAsset, ...],
    date: str,
    lookback_days: int,
    hold_days: int,
) -> tuple[EtfRotationHolding, ...]:
    features: list[tuple[EtfRotationAsset, float, float | None]] = []
    normalized_lookback = max(2, int(lookback_days or 2))
    normalized_hold = max(0, int(hold_days or 0))
    for asset in pool:
        rows = bars.get(asset.symbol, [])
        idx = indexes.get(asset.symbol, {}).get(date)
        if idx is None or idx < normalized_lookback:
            continue
        returns = _daily_return_window(rows, idx, normalized_lookback)
        if len(returns) < 2:
            continue
        realized_volatility = statistics.stdev(returns)
        if realized_volatility <= 1e-12:
            continue
        forward_return = None
        if normalized_hold > 0:
            if idx + normalized_hold >= len(rows):
                continue
            close = _float(rows[idx].get("close"))
            future_close = _float(rows[idx + normalized_hold].get("close"))
            if close <= 0 or future_close <= 0:
                continue
            forward_return = future_close / close - 1.0
        features.append((asset, 1.0 / (realized_volatility * realized_volatility), forward_return))
    total_inverse_variance = sum(weight for _asset, weight, _forward_return in features)
    if total_inverse_variance <= 0:
        return ()
    return tuple(
        EtfRotationHolding(
            market=asset.market,
            symbol=asset.symbol,
            name=asset.name,
            weight=weight / total_inverse_variance,
            fast_momentum=0.0,
            forward_return=forward_return,
        )
        for asset, weight, forward_return in sorted(features, key=lambda item: item[1], reverse=True)
    )


def _select_dual_momentum_switch_holdings(
    features: list[dict[str, Any]],
    *,
    top_n: int,
    momentum_threshold: float,
    offensive_symbols: frozenset[str],
    defensive_symbols: frozenset[str],
) -> tuple[EtfRotationHolding, ...]:
    normalized_top_n = max(1, int(top_n or 1))
    offensive = [
        item for item in features
        if item["asset"].symbol in offensive_symbols
        and item["fast_momentum"] > momentum_threshold
        and item["r6"] > 0
    ]
    offensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
    selected = offensive[:normalized_top_n]
    if selected:
        return tuple(_holding_from_feature(item, 1.0 / len(selected)) for item in selected)

    defensive = [item for item in features if item["asset"].symbol in defensive_symbols]
    defensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
    if defensive:
        return (_holding_from_feature(defensive[0], 1.0),)
    return ()


def _select_breadth_filtered_canary_holdings(
    *,
    bars: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    pool: tuple[EtfRotationAsset, ...],
    features: list[dict[str, Any]],
    date: str,
    top_n: int,
    canary_threshold: float,
    breadth_ma_days: int,
    min_breadth_ratio: float,
) -> tuple[EtfRotationHolding, ...]:
    breadth_ratio = _offensive_breadth_above_ma_ratio(
        bars=bars,
        indexes=indexes,
        pool=pool,
        date=date,
        ma_days=breadth_ma_days,
    )
    if breadth_ratio is not None and breadth_ratio < min_breadth_ratio:
        defensive = [item for item in features if item["asset"].role == "defensive"]
        defensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
        if defensive:
            return (_holding_from_feature(defensive[0], 1.0),)
    cash_fraction = _cash_fraction(features, canary_threshold=canary_threshold)
    return _select_holdings(features, cash_fraction=cash_fraction, top_n=top_n)


def _offensive_breadth_above_ma_ratio(
    *,
    bars: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    pool: tuple[EtfRotationAsset, ...],
    date: str,
    ma_days: int,
) -> float | None:
    normalized_ma_days = max(2, int(ma_days or 2))
    passed = 0
    total = 0
    for asset in pool:
        if asset.role != "offensive":
            continue
        rows = bars.get(asset.symbol, [])
        idx = indexes.get(asset.symbol, {}).get(date)
        if idx is None or idx < normalized_ma_days - 1:
            continue
        close = _float(rows[idx].get("close"))
        ma_values = [_float(rows[row_idx].get("close")) for row_idx in range(idx - normalized_ma_days + 1, idx + 1)]
        ma_values = [value for value in ma_values if value > 0]
        if close <= 0 or len(ma_values) < normalized_ma_days:
            continue
        total += 1
        if close > sum(ma_values) / len(ma_values):
            passed += 1
    if total == 0:
        return None
    return passed / total


def _daily_return_window(rows: list[dict[str, Any]], idx: int, lookback_days: int) -> list[float]:
    returns: list[float] = []
    for row_index in range(idx - lookback_days + 1, idx + 1):
        if row_index <= 0:
            return []
        previous_close = _float(rows[row_index - 1].get("close"))
        close = _float(rows[row_index].get("close"))
        if previous_close <= 0 or close <= 0:
            return []
        returns.append(close / previous_close - 1.0)
    return returns


def _scale_period_exposure(period: EtfRotationPeriod, exposure: float) -> EtfRotationPeriod:
    normalized_exposure = max(0.0, min(float(exposure), 1.0))
    return EtfRotationPeriod(
        date=period.date,
        year=period.year,
        cash_fraction=1.0 - (1.0 - period.cash_fraction) * normalized_exposure,
        return_value=period.return_value * normalized_exposure,
        holdings=tuple(
            EtfRotationHolding(
                market=holding.market,
                symbol=holding.symbol,
                name=holding.name,
                weight=holding.weight * normalized_exposure,
                fast_momentum=holding.fast_momentum,
                forward_return=holding.forward_return,
            )
            for holding in period.holdings
        ),
    )


def _features_for_date(
    bars: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    pool: tuple[EtfRotationAsset, ...],
    date: str,
    *,
    hold_days: int,
) -> list[dict[str, Any]]:
    features = []
    asset_by_symbol = {asset.symbol: asset for asset in pool}
    for symbol, asset in asset_by_symbol.items():
        item = _asset_features(bars.get(symbol, []), indexes.get(symbol, {}), asset, date, hold_days=hold_days)
        if item is not None:
            features.append(item)
    return features


def _asset_features(
    rows: list[dict[str, Any]],
    index: dict[str, int],
    asset: EtfRotationAsset,
    date: str,
    *,
    hold_days: int,
) -> dict[str, Any] | None:
    idx = index.get(date)
    if idx is None or idx < 252 or idx + hold_days >= len(rows):
        return None
    close = _float(rows[idx].get("close"))
    if close <= 0:
        return None
    returns = {}
    for days in (21, 63, 126, 252):
        past_close = _float(rows[idx - days].get("close"))
        if past_close <= 0:
            return None
        returns[days] = close / past_close - 1.0
    forward_return = None
    if hold_days > 0:
        future_close = _float(rows[idx + hold_days].get("close"))
        if future_close <= 0:
            return None
        forward_return = future_close / close - 1.0
    fast_momentum = 12 * returns[21] + 4 * returns[63] + 2 * returns[126] + returns[252]
    return {
        "asset": asset,
        "fast_momentum": fast_momentum,
        "r3": returns[63],
        "r6": returns[126],
        "forward_return": forward_return,
    }


def _cash_fraction(features: list[dict[str, Any]], *, canary_threshold: float) -> float:
    canaries = [
        item for item in features
        if item["asset"].symbol in DEFAULT_CANARY_SYMBOLS
    ]
    if not canaries:
        return 1.0
    bad_count = sum(1 for item in canaries if item["fast_momentum"] <= canary_threshold)
    if bad_count == 0:
        return 0.0
    if bad_count == 1:
        return 0.5
    return 1.0


def _select_holdings(
    features: list[dict[str, Any]],
    *,
    cash_fraction: float,
    top_n: int,
) -> tuple[EtfRotationHolding, ...]:
    holdings: list[EtfRotationHolding] = []
    invest_fraction = 1.0 - cash_fraction
    if invest_fraction > 0:
        offensive = [
            item for item in features
            if item["asset"].role == "offensive" and item["r6"] > 0 and item["r3"] > -0.08
        ]
        offensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
        selected = offensive[:top_n]
        for item in selected:
            holdings.append(_holding_from_feature(item, invest_fraction / max(1, len(selected))))
    if cash_fraction > 0:
        defensive = [item for item in features if item["asset"].role == "defensive"]
        defensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
        if defensive:
            holdings.append(_holding_from_feature(defensive[0], cash_fraction))
    return tuple(holdings)


def _select_hybrid_asset_allocation_holdings(
    features: list[dict[str, Any]],
    *,
    canary_symbol: str,
    momentum_threshold: float,
    top_n: int,
) -> tuple[EtfRotationHolding, ...]:
    canary = next((item for item in features if item["asset"].symbol == canary_symbol), None)
    risk_on = bool(canary and canary["fast_momentum"] > momentum_threshold)
    if risk_on:
        offensive = [
            item for item in features
            if item["asset"].role == "offensive"
            and item["fast_momentum"] > momentum_threshold
            and item["r6"] > 0
        ]
        offensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
        selected = offensive[:top_n]
        if selected:
            return tuple(_holding_from_feature(item, 1.0 / len(selected)) for item in selected)
    defensive = [item for item in features if item["asset"].role == "defensive"]
    defensive.sort(key=lambda item: item["fast_momentum"], reverse=True)
    if defensive:
        return (_holding_from_feature(defensive[0], 1.0),)
    return ()


def _cash_fraction_from_holdings(holdings: tuple[EtfRotationHolding, ...]) -> float:
    if not holdings:
        return 1.0
    defensive_symbols = {asset.symbol for asset in DEFAULT_ETF_ROTATION_POOL if asset.role == "defensive"}
    return sum(holding.weight for holding in holdings if holding.symbol in defensive_symbols)


def _holding_from_feature(item: dict[str, Any], weight: float) -> EtfRotationHolding:
    asset = item["asset"]
    return EtfRotationHolding(
        market=asset.market,
        symbol=asset.symbol,
        name=asset.name,
        weight=weight,
        fast_momentum=item["fast_momentum"],
        forward_return=item["forward_return"],
    )


def _month_end_dates(
    bars: dict[str, list[dict[str, Any]]],
    indexes: dict[str, dict[str, int]],
    *,
    start_date: str,
) -> list[str]:
    months: dict[str, str] = {}
    for date in _available_dates(bars, start_date=start_date):
        if sum(1 for symbol in bars if date in indexes.get(symbol, {})) >= 8:
            months[date[:7]] = date
    return sorted(months.values())


def _available_dates(bars: dict[str, list[dict[str, Any]]], *, start_date: str) -> list[str]:
    return sorted({
        str(row["time_key"])
        for rows in bars.values()
        for row in rows
        if str(row.get("time_key") or "") >= start_date
    })


def _build_indexes(bars: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    return {
        symbol: {str(row["time_key"]): idx for idx, row in enumerate(rows)}
        for symbol, rows in bars.items()
    }


def _compound(values: Any) -> float:
    curve = 1.0
    for value in values:
        curve *= 1.0 + float(value)
    return curve - 1.0


def _compound_by_year(periods: list[EtfRotationPeriod]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for period in periods:
        grouped[period.year].append(period.return_value)
    return {year: _compound(values) for year, values in sorted(grouped.items())}


def _period_to_dict(period: EtfRotationPeriod | None) -> dict[str, Any] | None:
    if period is None:
        return None
    return {
        "date": period.date,
        "year": period.year,
        "cash_fraction": period.cash_fraction,
        "return": period.return_value,
        "holdings": [
            {
                "market": holding.market,
                "symbol": holding.symbol,
                "name": holding.name,
                "weight": holding.weight,
                "fast_momentum": holding.fast_momentum,
                "forward_return": holding.forward_return,
            }
            for holding in period.holdings
        ],
    }


def _float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0
