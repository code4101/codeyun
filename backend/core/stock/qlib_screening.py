from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

from backend.core.settings import get_settings

from .akshare_market import AkshareStockHistoryRow, fetch_akshare_stock_history
from .hkex_board_lot import load_hkex_board_lots
from .index_benchmark import IndexBenchmark, load_index_benchmarks, load_index_rows, serialize_index_benchmark
from .market_data import MARKET_DATA_PROVIDER_AKSHARE, connect_market_data_db
from .qlib_bridge import (
    QLIB_EXPORT_START_DATE,
    QLIB_FACTOR_SCORE_RULES,
    QLIB_SCORE_PROFILE_LABELS,
    QlibFactorAnalysis,
    QlibBacktestResult,
    QlibWatchTarget,
    _analyze_rows,
    backtest_qlib_one_lot_score_strategy,
    backtest_qlib_rows_one_lot_score_strategy,
    _cache_daily_rows,
    _daily_factor_scores,
    _finite_float,
    serialize_qlib_backtest_result,
    normalize_score_profile,
)


WATCHLIST_POOL = "watchlist"
HK_POOL = "hk_pool"


@dataclass(frozen=True)
class QlibScreenTarget:
    market: str
    symbol: str
    name: str
    pool: str
    start_date: str = QLIB_EXPORT_START_DATE

    @property
    def qlib_target(self) -> QlibWatchTarget:
        return QlibWatchTarget(
            market=self.market,
            symbol=self.symbol,
            name=self.name,
            start_date=self.start_date,
        )


@dataclass(frozen=True)
class QlibScreenItem:
    target: QlibScreenTarget
    analysis: QlibFactorAnalysis


@dataclass(frozen=True)
class QlibScreenResult:
    pool: str
    source: str
    target_count: int
    analyzed_count: int
    failed_count: int
    items: tuple[QlibScreenItem, ...]
    error: str = ""


@dataclass(frozen=True)
class QlibPoolBacktestItem:
    target: QlibScreenTarget
    lot_size: int | None
    result: QlibBacktestResult | None
    error: str = ""


@dataclass(frozen=True)
class QlibPoolBacktestResult:
    pool: str
    source: str
    target_count: int
    tested_count: int
    skipped_count: int
    start_date: str
    end_date: str
    score_threshold: int
    score_profile: str
    take_profit_percent: float
    stop_loss_percent: float
    max_holding_days: int
    cost_rate: float
    total_profit: float
    total_invested: float
    total_fee: float
    max_capital_used: float
    trade_count: int
    closed_trade_count: int
    open_position_count: int
    benchmarks: tuple[IndexBenchmark, ...]
    items: tuple[QlibPoolBacktestItem, ...]
    force_liquidate_end: bool = True
    error: str = ""


@dataclass(frozen=True)
class QlibStrategyCandidate:
    key: str
    name: str
    score_threshold: int
    score_profile: str
    take_profit_percent: float
    stop_loss_percent: float
    max_holding_days: int
    cost_rate: float


@dataclass(frozen=True)
class QlibStrategyYearResult:
    year: int
    start_date: str
    end_date: str
    total_profit: float
    return_percent: float | None
    max_capital_used: float
    total_fee: float
    trade_count: int
    tested_count: int
    skipped_count: int
    benchmark_name: str
    benchmark_return_percent: float | None
    excess_return_percent: float | None


@dataclass(frozen=True)
class QlibStrategySearchItem:
    candidate: QlibStrategyCandidate
    years: tuple[QlibStrategyYearResult, ...]
    total_profit: float
    average_return_percent: float | None
    min_return_percent: float | None
    average_excess_return_percent: float | None
    min_excess_return_percent: float | None
    profitable_year_count: int
    beat_benchmark_year_count: int
    tested_year_count: int
    all_years_profitable: bool
    all_years_beat_benchmark: bool
    is_qualified: bool
    qualification_note: str


@dataclass(frozen=True)
class QlibStrategySearchResult:
    pool: str
    source: str
    years: tuple[int, ...]
    limit: int | None
    benchmark_name: str
    items: tuple[QlibStrategySearchItem, ...]
    min_annual_return_percent: float = 5.0
    require_beat_benchmark: bool = True
    qualified_count: int = 0
    done_count: int = 0
    candidate_count: int = 0
    status: str = "done"
    error: str = ""


@dataclass(frozen=True)
class QlibRankedRotationPosition:
    target: QlibScreenTarget
    lot_size: int
    buy_date: str
    buy_price: float
    buy_cost: float
    score: int


@dataclass(frozen=True)
class QlibRankedRotationBacktestResult:
    pool: str
    source: str
    start_date: str
    end_date: str
    score_profile: str
    rank_metric: str
    market_filter: str
    score_threshold: int
    top_n: int
    rebalance: str
    min_amount: float
    cost_rate: float
    target_count: int
    tested_count: int
    skipped_count: int
    total_profit: float
    return_percent: float | None
    max_capital_used: float
    total_fee: float
    trade_count: int
    closed_trade_count: int
    benchmarks: tuple[IndexBenchmark, ...]
    error: str = ""


@dataclass(frozen=True)
class QlibRankedRotationBacktestContext:
    targets: tuple[QlibScreenTarget, ...]
    target_by_key: dict[tuple[str, str], QlibScreenTarget]
    board_lots: dict[str, int]
    rows_by_key: dict[tuple[str, str], tuple[AkshareStockHistoryRow, ...]]
    row_maps: dict[tuple[str, str], dict[str, AkshareStockHistoryRow]]
    previous_score_by_profile: dict[str, dict[tuple[str, str], dict[str, int | None]]]
    previous_relative_momentum_by_date: dict[tuple[str, str], dict[str, float | None]]
    previous_volume_breakout_by_date: dict[tuple[str, str], dict[str, float | None]]
    previous_value_quality_by_date: dict[tuple[str, str], dict[str, float | None]]
    index_return_by_date: dict[str, float | None]
    market_allowed_by_filter: dict[str, dict[str, bool]]
    skipped_count: int


class _RankedRotationPeriodCache:
    def __init__(self, context: QlibRankedRotationBacktestContext) -> None:
        self.context = context
        self._ranked_keys: dict[tuple[str, str, str, int, float], tuple[tuple[str, str], ...]] = {}
        self._period_returns: dict[tuple[tuple[str, str], str, str], float | None] = {}

    def selected_returns(
        self,
        *,
        current_date: str,
        next_date: str,
        score_profile: str,
        rank_metric: str,
        score_threshold: int,
        min_amount: float,
        top_n: int,
    ) -> list[float]:
        ranked_keys = self._ranked_keys_for(
            current_date=current_date,
            score_profile=score_profile,
            rank_metric=rank_metric,
            score_threshold=score_threshold,
            min_amount=min_amount,
        )
        returns: list[float] = []
        for key in ranked_keys[: max(1, int(top_n))]:
            cache_key = (key, current_date, next_date)
            if cache_key not in self._period_returns:
                self._period_returns[cache_key] = _realized_return_between(self.context.row_maps[key], current_date, next_date)
            period_return = self._period_returns[cache_key]
            if period_return is not None:
                returns.append(period_return)
        return returns

    def _ranked_keys_for(
        self,
        *,
        current_date: str,
        score_profile: str,
        rank_metric: str,
        score_threshold: int,
        min_amount: float,
    ) -> tuple[tuple[str, str], ...]:
        cache_key = (current_date, score_profile, rank_metric, int(score_threshold), float(min_amount))
        cached = self._ranked_keys.get(cache_key)
        if cached is not None:
            return cached
        ranked: list[tuple[float, tuple[str, str]]] = []
        previous_score_by_date = self.context.previous_score_by_profile.get(score_profile, {})
        for key, row_map in self.context.row_maps.items():
            row = row_map.get(current_date)
            if row is None:
                continue
            amount = _finite_float(row.amount)
            if amount is not None and amount < min_amount:
                continue
            score = previous_score_by_date.get(key, {}).get(current_date)
            if score is None or score < score_threshold:
                continue
            rank_value = _ranked_rotation_rank_value(
                context=self.context,
                key=key,
                current_date=current_date,
                score=score,
                rank_metric=rank_metric,
            )
            if rank_value is not None:
                ranked.append((float(rank_value), key))
        ranked.sort(key=lambda item: item[0], reverse=True)
        result = tuple(key for _rank_value, key in ranked)
        self._ranked_keys[cache_key] = result
        return result


@dataclass(frozen=True)
class QlibRankedRotationDiagnosisPeriod:
    start_date: str
    end_date: str
    selected: tuple[tuple[str, str, float | None], ...]
    realized_best: tuple[tuple[str, str, float], ...]
    selected_average_return: float | None
    best_average_return: float | None
    hit_count: int


@dataclass(frozen=True)
class QlibRotationStrategyCandidate:
    key: str
    name: str
    score_profile: str
    rank_metric: str
    market_filter: str
    score_threshold: int
    min_amount: float
    top_n: int
    rebalance: str
    cost_rate: float


@dataclass(frozen=True)
class QlibRotationStrategySearchItem:
    candidate: QlibRotationStrategyCandidate
    years: tuple[QlibStrategyYearResult, ...]
    total_profit: float
    average_return_percent: float | None
    min_return_percent: float | None
    average_excess_return_percent: float | None
    min_excess_return_percent: float | None
    profitable_year_count: int
    beat_benchmark_year_count: int
    tested_year_count: int
    all_years_profitable: bool
    all_years_beat_benchmark: bool
    is_qualified: bool
    qualification_note: str


@dataclass(frozen=True)
class QlibRotationStrategySearchResult:
    pool: str
    source: str
    years: tuple[int, ...]
    limit: int | None
    benchmark_name: str
    items: tuple[QlibRotationStrategySearchItem, ...]
    min_annual_return_percent: float = 5.0
    require_beat_benchmark: bool = True
    qualified_count: int = 0
    done_count: int = 0
    candidate_count: int = 0
    status: str = "done"
    error: str = ""


@dataclass(frozen=True)
class HkConnectMomentumReviewCandidate:
    rank: int
    market: str
    symbol: str
    name: str
    signal_score: float
    return_10_percent: float
    amount: float
    average_amount_20: float
    close: float
    lot_size: int
    lot_value: float
    budget_lots: int
    estimated_cash: float
    market_cap: float
    selected: bool


@dataclass(frozen=True)
class HkConnectMomentumReviewResult:
    strategy_key: str
    strategy_name: str
    source: str
    status: str
    generated_at: str
    signal_date: str
    hsi_date: str
    hsi_close: float | None
    hsi_ma60: float | None
    hsi_filter_passed: bool
    action: str
    summary: str
    pool_count: int
    usable_count: int
    capital: float
    max_position_percent: float
    single_position_budget: float
    cost_rate: float
    universe_limit: int
    min_market_cap: float
    min_amount: float
    top_n: int
    lookback_days: int
    volume_window_days: int
    hold_days: int
    candidates: tuple[HkConnectMomentumReviewCandidate, ...]
    selected: tuple[HkConnectMomentumReviewCandidate, ...]
    error: str = ""


DEFAULT_STRATEGY_SCORE_THRESHOLDS = (70, 76, 80, 84, 88, 90)
DEFAULT_STRATEGY_TAKE_PROFIT_PERCENTS = (5.0, 8.0, 10.0, 15.0)
DEFAULT_STRATEGY_STOP_LOSS_PERCENTS = (0.0, 8.0)
DEFAULT_STRATEGY_MAX_HOLDING_DAYS = (0, 60)
DEFAULT_STRATEGY_SCORE_PROFILES = ("balanced", "trend_momentum", "short_reversal", "low_volatility", "volume_breakout")
DEFAULT_STRATEGY_COST_RATE = 0.01
DEFAULT_ROTATION_RANK_METRICS = ("score", "volume_breakout_rank", "value_score_rank")
DEFAULT_ROTATION_MARKET_FILTERS = ("none", "hsi_ma60")
DEFAULT_ROTATION_SCORE_THRESHOLDS = (0, 70, 76)
DEFAULT_ROTATION_MIN_AMOUNTS = (10_000_000.0,)
DEFAULT_ROTATION_TOP_N = (3, 5, 10)
DEFAULT_ROTATION_REBALANCES = ("monthly", "quarterly")
VALUE_QUALITY_MAX_STALE_DAYS = 120


def build_qlib_strategy_candidates(
    *,
    score_thresholds: tuple[int, ...] = DEFAULT_STRATEGY_SCORE_THRESHOLDS,
    take_profit_percents: tuple[float, ...] = DEFAULT_STRATEGY_TAKE_PROFIT_PERCENTS,
    stop_loss_percents: tuple[float, ...] = DEFAULT_STRATEGY_STOP_LOSS_PERCENTS,
    max_holding_days_values: tuple[int, ...] = DEFAULT_STRATEGY_MAX_HOLDING_DAYS,
    score_profiles: tuple[str, ...] = DEFAULT_STRATEGY_SCORE_PROFILES,
    cost_rate: float = DEFAULT_STRATEGY_COST_RATE,
) -> tuple[QlibStrategyCandidate, ...]:
    candidates: list[QlibStrategyCandidate] = []
    normalized_profiles = tuple(dict.fromkeys(normalize_score_profile(value) for value in score_profiles))
    for score_profile in normalized_profiles:
        profile_label = QLIB_SCORE_PROFILE_LABELS.get(score_profile, score_profile)
        for score_threshold in sorted({int(value) for value in score_thresholds}):
            for take_profit_percent in sorted({float(value) for value in take_profit_percents}):
                for stop_loss_percent in sorted({float(value) for value in stop_loss_percents}):
                    for max_holding_days in sorted({max(0, int(value)) for value in max_holding_days_values}):
                        take_profit_label = f"{take_profit_percent:g}"
                        stop_loss_label = f"{stop_loss_percent:g}"
                        cost_label = f"{cost_rate * 100:g}"
                        hold_label = int(max_holding_days)
                        candidates.append(QlibStrategyCandidate(
                            key=f"{score_profile}_score{score_threshold}_tp{take_profit_label}_sl{stop_loss_label}_hold{hold_label}_cost{cost_label}",
                            name=f"{profile_label} / {score_threshold}分 / 止盈{take_profit_label}% / 止损{stop_loss_label}% / {hold_label or '不限'}日 / 成本{cost_label}%",
                            score_threshold=score_threshold,
                            score_profile=score_profile,
                            take_profit_percent=take_profit_percent,
                            stop_loss_percent=stop_loss_percent,
                            max_holding_days=hold_label,
                            cost_rate=cost_rate,
                        ))
    return tuple(candidates)


DEFAULT_STRATEGY_CANDIDATES = build_qlib_strategy_candidates(
    score_thresholds=DEFAULT_STRATEGY_SCORE_THRESHOLDS,
    take_profit_percents=DEFAULT_STRATEGY_TAKE_PROFIT_PERCENTS,
    stop_loss_percents=DEFAULT_STRATEGY_STOP_LOSS_PERCENTS,
    max_holding_days_values=DEFAULT_STRATEGY_MAX_HOLDING_DAYS,
    score_profiles=DEFAULT_STRATEGY_SCORE_PROFILES,
    cost_rate=DEFAULT_STRATEGY_COST_RATE,
)


def build_qlib_rotation_strategy_candidates(
    *,
    score_profiles: tuple[str, ...] = ("balanced",),
    rank_metrics: tuple[str, ...] = DEFAULT_ROTATION_RANK_METRICS,
    market_filters: tuple[str, ...] = DEFAULT_ROTATION_MARKET_FILTERS,
    score_thresholds: tuple[int, ...] = DEFAULT_ROTATION_SCORE_THRESHOLDS,
    min_amounts: tuple[float, ...] = DEFAULT_ROTATION_MIN_AMOUNTS,
    top_n_values: tuple[int, ...] = DEFAULT_ROTATION_TOP_N,
    rebalances: tuple[str, ...] = DEFAULT_ROTATION_REBALANCES,
    cost_rate: float = DEFAULT_STRATEGY_COST_RATE,
) -> tuple[QlibRotationStrategyCandidate, ...]:
    candidates: list[QlibRotationStrategyCandidate] = []
    normalized_profiles = tuple(dict.fromkeys(normalize_score_profile(value) for value in score_profiles))
    normalized_rank_metrics = tuple(
        dict.fromkeys(
            value
            for value in (str(item).strip().lower() for item in rank_metrics)
            if value in {
                "score",
                "relative_momentum_60",
                "relative_reversal_60",
                "volume_breakout_rank",
                "value_quality_rank",
                "value_score_rank",
                "adaptive_value_breakout",
            }
        )
    ) or ("score",)
    normalized_market_filters = tuple(
        dict.fromkeys(
            value
            for value in (str(item).strip().lower() for item in market_filters)
            if value in {"none", "hsi_ma60", "hsi_ma120", "hsi_ma200"}
        )
    ) or ("none",)
    normalized_rebalances = tuple(
        dict.fromkeys(
            value
            for value in (str(item).strip().lower() for item in rebalances)
            if value in {"weekly", "monthly", "quarterly"}
        )
    ) or ("quarterly",)
    for score_profile in normalized_profiles:
        profile_label = QLIB_SCORE_PROFILE_LABELS.get(score_profile, score_profile)
        for rank_metric in normalized_rank_metrics:
            for market_filter in normalized_market_filters:
                for score_threshold in sorted({max(0, min(100, int(value))) for value in score_thresholds}):
                    for min_amount in sorted({max(0, float(value)) for value in min_amounts}):
                        for top_n in sorted({max(1, int(value)) for value in top_n_values}):
                            for rebalance in normalized_rebalances:
                                amount_label = f"{min_amount / 10000:g}万"
                                cost_label = f"{cost_rate * 100:g}"
                                candidates.append(QlibRotationStrategyCandidate(
                                    key=(
                                        f"{score_profile}_{rank_metric}_{market_filter}"
                                        f"_score{score_threshold}_amt{int(min_amount)}_top{top_n}_{rebalance}_cost{cost_label}"
                                    ),
                                    name=(
                                        f"{profile_label} / {rank_metric} / {market_filter} / "
                                        f"{score_threshold}分 / {amount_label}成交额 / Top{top_n} / {rebalance} / 成本{cost_label}%"
                                    ),
                                    score_profile=score_profile,
                                    rank_metric=rank_metric,
                                    market_filter=market_filter,
                                    score_threshold=score_threshold,
                                    min_amount=min_amount,
                                    top_n=top_n,
                                    rebalance=rebalance,
                                    cost_rate=cost_rate,
                                ))
    return tuple(candidates)


def list_hk_screen_targets(*, limit: int | None = None) -> tuple[QlibScreenTarget, ...]:
    rows, _source = _load_hk_pool_rows()
    targets: list[QlibScreenTarget] = []
    seen: set[str] = set()
    for row in rows:
        symbol = _normalize_hk_symbol(row.get("代码") or row.get("code") or row.get("symbol"))
        if not symbol or symbol in seen:
            continue
        name = str(row.get("名称") or row.get("中文名称") or row.get("name") or symbol).strip() or symbol
        seen.add(symbol)
        targets.append(QlibScreenTarget(market="HK", symbol=symbol, name=name, pool=HK_POOL))
    targets.sort(key=lambda item: item.symbol)
    if limit is not None:
        targets = targets[: max(0, int(limit))]
    return tuple(targets)


def search_hk_pool_one_lot_score_strategies(
    *,
    years: tuple[int, ...] = (2023, 2024, 2025),
    limit: int | None = None,
    candidates: tuple[QlibStrategyCandidate, ...] = DEFAULT_STRATEGY_CANDIDATES,
    refresh: bool = False,
    force_liquidate_end: bool = True,
    min_annual_return_percent: float = 5.0,
    require_beat_benchmark: bool = True,
    progress_callback: Callable[[QlibStrategySearchResult], None] | None = None,
) -> QlibStrategySearchResult:
    normalized_years = tuple(sorted({int(year) for year in years if int(year) >= 1990}))
    cache_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_strategy_search.json"
    cache_key = _strategy_search_cache_key(
        years=normalized_years,
        limit=limit,
        candidates=candidates,
        force_liquidate_end=force_liquidate_end,
        min_annual_return_percent=min_annual_return_percent,
        require_beat_benchmark=require_beat_benchmark,
    )
    if not refresh:
        cached = _read_strategy_search_cache(cache_path, cache_key)
        if cached is not None:
            return cached

    items: list[QlibStrategySearchItem] = []
    candidate_count = len(candidates)
    for candidate in candidates:
        year_results: list[QlibStrategyYearResult] = []
        for year in normalized_years:
            start_date = f"{year}-01-01"
            end_date = f"{year}-12-31"
            backtest = backtest_hk_pool_one_lot_score(
                refresh=False,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                score_threshold=candidate.score_threshold,
                score_profile=candidate.score_profile,
                take_profit_percent=candidate.take_profit_percent,
                stop_loss_percent=candidate.stop_loss_percent,
                max_holding_days=candidate.max_holding_days,
                cost_rate=candidate.cost_rate,
                force_liquidate_end=force_liquidate_end,
            )
            return_percent = backtest.total_profit / backtest.max_capital_used * 100 if backtest.max_capital_used else None
            benchmark = next((item for item in backtest.benchmarks if item.name == "恒生指数"), None)
            if benchmark is None and backtest.benchmarks:
                benchmark = backtest.benchmarks[0]
            benchmark_return = benchmark.return_percent if benchmark is not None else None
            excess_return = (
                return_percent - benchmark_return
                if return_percent is not None and benchmark_return is not None
                else None
            )
            year_results.append(QlibStrategyYearResult(
                year=year,
                start_date=backtest.start_date,
                end_date=backtest.end_date,
                total_profit=backtest.total_profit,
                return_percent=return_percent,
                max_capital_used=backtest.max_capital_used,
                total_fee=backtest.total_fee,
                trade_count=backtest.trade_count,
                tested_count=backtest.tested_count,
                skipped_count=backtest.skipped_count,
                benchmark_name=benchmark.name if benchmark is not None else "恒生指数",
                benchmark_return_percent=benchmark_return,
                excess_return_percent=excess_return,
            ))
        valid_returns = [item.return_percent for item in year_results if item.return_percent is not None]
        valid_excess_returns = [item.excess_return_percent for item in year_results if item.excess_return_percent is not None]
        profitable_year_count = sum(1 for item in year_results if item.total_profit > 0)
        beat_benchmark_year_count = sum(1 for item in year_results if item.excess_return_percent is not None and item.excess_return_percent > 0)
        tested_year_count = len(year_results)
        all_years_profitable = tested_year_count > 0 and profitable_year_count == tested_year_count
        all_years_beat_benchmark = tested_year_count > 0 and beat_benchmark_year_count == tested_year_count
        min_return_percent = min(valid_returns) if valid_returns else None
        is_qualified = (
            all_years_profitable
            and min_return_percent is not None
            and min_return_percent >= min_annual_return_percent
            and (all_years_beat_benchmark or not require_beat_benchmark)
        )
        qualification_note = _strategy_qualification_note(
            is_qualified=is_qualified,
            all_years_profitable=all_years_profitable,
            all_years_beat_benchmark=all_years_beat_benchmark,
            min_return_percent=min_return_percent,
            min_annual_return_percent=min_annual_return_percent,
            require_beat_benchmark=require_beat_benchmark,
        )
        items.append(QlibStrategySearchItem(
            candidate=candidate,
            years=tuple(year_results),
            total_profit=sum(item.total_profit for item in year_results),
            average_return_percent=sum(valid_returns) / len(valid_returns) if valid_returns else None,
            min_return_percent=min_return_percent,
            average_excess_return_percent=sum(valid_excess_returns) / len(valid_excess_returns) if valid_excess_returns else None,
            min_excess_return_percent=min(valid_excess_returns) if valid_excess_returns else None,
            profitable_year_count=profitable_year_count,
            beat_benchmark_year_count=beat_benchmark_year_count,
            tested_year_count=tested_year_count,
            all_years_profitable=all_years_profitable,
            all_years_beat_benchmark=all_years_beat_benchmark,
            is_qualified=is_qualified,
            qualification_note=qualification_note,
        ))
        if progress_callback is not None:
            progress_callback(_build_strategy_search_result(
                years=normalized_years,
                limit=limit,
                items=items,
                source=f"running:{len(items)}/{candidate_count}; cache:hk_pool_backtest_one_lot_score",
                done_count=len(items),
                candidate_count=candidate_count,
                status="running",
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            ))
    result = _build_strategy_search_result(
        years=normalized_years,
        limit=limit,
        items=items,
        source="cache:hk_pool_backtest_one_lot_score",
        done_count=candidate_count,
        candidate_count=candidate_count,
        status="done",
        min_annual_return_percent=min_annual_return_percent,
        require_beat_benchmark=require_beat_benchmark,
    )
    _write_strategy_search_cache(cache_path, cache_key, result)
    if progress_callback is not None:
        progress_callback(result)
    return result


def _build_strategy_search_result(
    *,
    years: tuple[int, ...],
    limit: int | None,
    items: list[QlibStrategySearchItem] | tuple[QlibStrategySearchItem, ...],
    source: str,
    done_count: int,
    candidate_count: int,
    status: str,
    min_annual_return_percent: float = 5.0,
    require_beat_benchmark: bool = True,
    error: str = "",
) -> QlibStrategySearchResult:
    ranked_items = sorted(
        items,
        key=lambda item: (
            item.is_qualified,
            item.beat_benchmark_year_count,
            item.profitable_year_count,
            item.min_excess_return_percent if item.min_excess_return_percent is not None else float("-inf"),
            item.min_return_percent if item.min_return_percent is not None else float("-inf"),
            item.average_return_percent if item.average_return_percent is not None else float("-inf"),
            item.total_profit,
        ),
        reverse=True,
    )
    return QlibStrategySearchResult(
        pool=HK_POOL,
        source=source,
        years=years,
        limit=limit,
        benchmark_name="恒生指数",
        items=tuple(ranked_items),
        min_annual_return_percent=float(min_annual_return_percent),
        require_beat_benchmark=bool(require_beat_benchmark),
        qualified_count=sum(1 for item in ranked_items if item.is_qualified),
        done_count=done_count,
        candidate_count=candidate_count,
        status=status,
        error=error,
    )


def _strategy_qualification_note(
    *,
    is_qualified: bool,
    all_years_profitable: bool,
    all_years_beat_benchmark: bool,
    min_return_percent: float | None,
    min_annual_return_percent: float,
    require_beat_benchmark: bool,
) -> str:
    if is_qualified:
        return "达标"
    if not all_years_profitable:
        return "存在亏损年份"
    if min_return_percent is None:
        return "缺少有效收益率"
    if min_return_percent < min_annual_return_percent:
        return f"最差年份低于{min_annual_return_percent:g}%"
    if require_beat_benchmark and not all_years_beat_benchmark:
        return "未每年跑赢恒生"
    return "未达标"


def search_hk_pool_ranked_rotation_strategies(
    *,
    years: tuple[int, ...] = (2023, 2024, 2025),
    limit: int | None = 300,
    candidates: tuple[QlibRotationStrategyCandidate, ...] | None = None,
    min_annual_return_percent: float = 5.0,
    require_beat_benchmark: bool = True,
    progress_callback: Callable[[QlibRotationStrategySearchResult], None] | None = None,
) -> QlibRotationStrategySearchResult:
    normalized_years = tuple(sorted({int(year) for year in years if int(year) >= 1990}))
    candidate_values = candidates or build_qlib_rotation_strategy_candidates()
    items: list[QlibRotationStrategySearchItem] = []
    candidate_count = len(candidate_values)
    max_end_date = f"{max(normalized_years)}-12-31" if normalized_years else dt.date.today().isoformat()
    context = _prepare_ranked_rotation_backtest_context(
        limit=limit,
        end_date=max_end_date,
        score_profiles=tuple(candidate.score_profile for candidate in candidate_values),
        rank_metrics=tuple(candidate.rank_metric for candidate in candidate_values),
        market_filters=tuple(candidate.market_filter for candidate in candidate_values),
    )
    for candidate in candidate_values:
        year_results: list[QlibStrategyYearResult] = []
        for year in normalized_years:
            result = backtest_hk_pool_ranked_rotation_strategy(
                context=context,
                start_date=f"{year}-01-01",
                end_date=f"{year}-12-31",
                score_profile=candidate.score_profile,
                rank_metric=candidate.rank_metric,
                market_filter=candidate.market_filter,
                score_threshold=candidate.score_threshold,
                min_amount=candidate.min_amount,
                top_n=candidate.top_n,
                rebalance=candidate.rebalance,
                cost_rate=candidate.cost_rate,
            )
            benchmark = next((item for item in result.benchmarks if item.name == "恒生指数"), None)
            if benchmark is None and result.benchmarks:
                benchmark = result.benchmarks[0]
            benchmark_return = benchmark.return_percent if benchmark is not None else None
            excess_return = (
                result.return_percent - benchmark_return
                if result.return_percent is not None and benchmark_return is not None
                else None
            )
            year_results.append(QlibStrategyYearResult(
                year=year,
                start_date=result.start_date,
                end_date=result.end_date,
                total_profit=result.total_profit,
                return_percent=result.return_percent,
                max_capital_used=result.max_capital_used,
                total_fee=result.total_fee,
                trade_count=result.trade_count,
                tested_count=result.tested_count,
                skipped_count=result.skipped_count,
                benchmark_name=benchmark.name if benchmark is not None else "恒生指数",
                benchmark_return_percent=benchmark_return,
                excess_return_percent=excess_return,
            ))

        valid_returns = [item.return_percent for item in year_results if item.return_percent is not None]
        valid_excess_returns = [item.excess_return_percent for item in year_results if item.excess_return_percent is not None]
        profitable_year_count = sum(1 for item in year_results if item.return_percent is not None and item.return_percent > 0)
        beat_benchmark_year_count = sum(1 for item in year_results if item.excess_return_percent is not None and item.excess_return_percent > 0)
        tested_year_count = len(valid_returns)
        all_years_profitable = tested_year_count > 0 and tested_year_count == len(normalized_years) and profitable_year_count == len(normalized_years)
        all_years_beat_benchmark = len(valid_excess_returns) == len(normalized_years) and beat_benchmark_year_count == len(normalized_years)
        min_return_percent = min(valid_returns) if valid_returns else None
        is_qualified = (
            all_years_profitable
            and min_return_percent is not None
            and min_return_percent >= min_annual_return_percent
            and (all_years_beat_benchmark or not require_beat_benchmark)
        )
        items.append(QlibRotationStrategySearchItem(
            candidate=candidate,
            years=tuple(year_results),
            total_profit=sum(item.total_profit for item in year_results),
            average_return_percent=sum(valid_returns) / len(valid_returns) if valid_returns else None,
            min_return_percent=min_return_percent,
            average_excess_return_percent=sum(valid_excess_returns) / len(valid_excess_returns) if valid_excess_returns else None,
            min_excess_return_percent=min(valid_excess_returns) if valid_excess_returns else None,
            profitable_year_count=profitable_year_count,
            beat_benchmark_year_count=beat_benchmark_year_count,
            tested_year_count=tested_year_count,
            all_years_profitable=all_years_profitable,
            all_years_beat_benchmark=all_years_beat_benchmark,
            is_qualified=is_qualified,
            qualification_note=_strategy_qualification_note(
                is_qualified=is_qualified,
                all_years_profitable=all_years_profitable,
                all_years_beat_benchmark=all_years_beat_benchmark,
                min_return_percent=min_return_percent,
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            ),
        ))
        if progress_callback is not None:
            progress_callback(_build_rotation_strategy_search_result(
                years=normalized_years,
                limit=limit,
                items=items,
                done_count=len(items),
                candidate_count=candidate_count,
                status="running",
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            ))

    result = _build_rotation_strategy_search_result(
        years=normalized_years,
        limit=limit,
        items=items,
        done_count=candidate_count,
        candidate_count=candidate_count,
        status="done",
        min_annual_return_percent=min_annual_return_percent,
        require_beat_benchmark=require_beat_benchmark,
    )
    if progress_callback is not None:
        progress_callback(result)
    return result


def compute_hk_connect_momentum_review(
    *,
    refresh: bool = False,
    end_date: str | None = None,
    capital: float = 100_000,
    max_position_percent: float = 0.7,
    universe_limit: int = 300,
    min_market_cap: float = 50_000_000_000,
    min_amount: float = 500_000_000,
    top_n: int = 2,
    lookback_days: int = 10,
    volume_window_days: int = 20,
    hold_days: int = 20,
    cost_rate: float = 0.0025,
    progress_callback: Callable[[HkConnectMomentumReviewResult], None] | None = None,
) -> HkConnectMomentumReviewResult:
    normalized_end_date = _normalize_iso_date(end_date) or dt.date.today().isoformat()
    generated_at = dt.datetime.now().isoformat(timespec="seconds")
    top_n_value = max(1, int(top_n))
    universe_limit_value = max(1, int(universe_limit))
    capital_value = max(0, float(capital))
    max_position_value = min(1.0, max(0.0, float(max_position_percent)))
    single_budget = capital_value * max_position_value / top_n_value if top_n_value else 0
    strategy_key = "hk_connect_hsi60_largecap_volmom_top2"
    strategy_name = "港股通恒生60日线大市值成交额动量"

    def build_status(
        *,
        source: str,
        status: str,
        signal_date: str = "",
        hsi_date: str = "",
        hsi_close: float | None = None,
        hsi_ma60: float | None = None,
        hsi_filter_passed: bool = False,
        action: str = "wait",
        summary: str = "",
        pool_count: int = 0,
        usable_count: int = 0,
        candidates: tuple[HkConnectMomentumReviewCandidate, ...] = (),
        selected: tuple[HkConnectMomentumReviewCandidate, ...] = (),
        error: str = "",
    ) -> HkConnectMomentumReviewResult:
        return HkConnectMomentumReviewResult(
            strategy_key=strategy_key,
            strategy_name=strategy_name,
            source=source,
            status=status,
            generated_at=generated_at,
            signal_date=signal_date,
            hsi_date=hsi_date,
            hsi_close=hsi_close,
            hsi_ma60=hsi_ma60,
            hsi_filter_passed=hsi_filter_passed,
            action=action,
            summary=summary,
            pool_count=pool_count,
            usable_count=usable_count,
            capital=capital_value,
            max_position_percent=max_position_value,
            single_position_budget=single_budget,
            cost_rate=max(0, float(cost_rate)),
            universe_limit=universe_limit_value,
            min_market_cap=max(0, float(min_market_cap)),
            min_amount=max(0, float(min_amount)),
            top_n=top_n_value,
            lookback_days=max(1, int(lookback_days)),
            volume_window_days=max(1, int(volume_window_days)),
            hold_days=max(1, int(hold_days)),
            candidates=candidates,
            selected=selected,
            error=error,
        )

    components = _load_hk_connect_turnover_components(limit=universe_limit_value)
    filtered_components = tuple(
        row
        for row in components
        if _finite_float(row.get("market_cap")) is not None
        and (_finite_float(row.get("market_cap")) or 0) >= max(0, float(min_market_cap))
    )
    if progress_callback is not None:
        progress_callback(build_status(
            source="running:components",
            status="running",
            pool_count=len(filtered_components),
            summary=f"已读取港股通高成交额成分 {len(filtered_components)} 个",
        ))
    if not filtered_components:
        return build_status(
            source="eastmoney:hk_connect_components",
            status="error",
            error="港股通成分池为空",
            summary="港股通成分池为空，无法计算策略复盘",
        )

    targets = tuple(
        QlibScreenTarget(market="HK", symbol=str(row["symbol"]), name=str(row["name"]), pool="hk_connect_momentum")
        for row in filtered_components
    )
    target_meta = {str(row["symbol"]): row for row in filtered_components}
    rows_by_key: dict[tuple[str, str], tuple[AkshareStockHistoryRow, ...]] = {}
    with connect_market_data_db() as conn:
        cached_rows = _read_cached_daily_rows_for_targets_with_conn(conn, targets)
    for index, target in enumerate(targets, start=1):
        rows = tuple(row for row in cached_rows.get((target.market, target.symbol), ()) if row.date <= normalized_end_date)
        if refresh:
            try:
                history = fetch_akshare_stock_history(
                    market=target.market,
                    symbol=target.symbol,
                    name=target.name,
                    period="daily",
                    start_date=_review_start_date(normalized_end_date),
                    end_date=normalized_end_date,
                    adjust="",
                )
                if history.rows and (not rows or history.rows[-1].date >= rows[-1].date):
                    rows = tuple(history.rows)
            except Exception:
                pass
        rows = tuple(row for row in rows if row.date <= normalized_end_date and _finite_float(row.open) is not None and _finite_float(row.close) is not None)
        if len(rows) >= max(int(lookback_days), int(volume_window_days)) + 1:
            rows_by_key[(target.market, target.symbol)] = rows
        if progress_callback is not None and (index == len(targets) or index % 25 == 0):
            progress_callback(build_status(
                source=f"running:history:{index}/{len(targets)}",
                status="running",
                pool_count=len(filtered_components),
                usable_count=len(rows_by_key),
                summary=f"已准备日线 {index}/{len(targets)}，可用 {len(rows_by_key)} 个",
            ))

    hsi_rows = tuple(row for row in load_index_rows(market="HK", symbol="HSI", refresh=refresh) if row.get("date") and row.get("close") is not None and str(row["date"]) <= normalized_end_date)
    if len(hsi_rows) < 60:
        return build_status(
            source="akshare:index:hsi",
            status="error",
            pool_count=len(filtered_components),
            usable_count=len(rows_by_key),
            error="恒生指数数据不足 60 日",
            summary="恒生指数数据不足，无法判断大盘过滤",
        )
    hsi_rows = tuple(sorted(hsi_rows, key=lambda row: str(row["date"])))
    hsi_latest_rows = hsi_rows[-60:]
    hsi_date = str(hsi_latest_rows[-1]["date"])
    hsi_close = _finite_float(hsi_latest_rows[-1].get("close"))
    hsi_ma60 = sum(float(row["close"]) for row in hsi_latest_rows) / 60
    hsi_filter_passed = hsi_close is not None and hsi_close > hsi_ma60

    latest_stock_date = max((row.date for rows in rows_by_key.values() for row in rows), default="")
    signal_date = min(latest_stock_date, hsi_date) if latest_stock_date and hsi_date else latest_stock_date or hsi_date
    if not signal_date:
        return build_status(
            source="cache:market_kline",
            status="error",
            pool_count=len(filtered_components),
            usable_count=len(rows_by_key),
            error="没有可用股票日线",
            summary="没有可用股票日线，无法计算策略复盘",
        )

    board_lots = load_hkex_board_lots(refresh=False)
    ranked_values: list[tuple[float, HkConnectMomentumReviewCandidate]] = []
    for key, rows in rows_by_key.items():
        symbol = key[1]
        row_index_by_date = {row.date: index for index, row in enumerate(rows)}
        index = row_index_by_date.get(signal_date)
        if index is None or index < max(int(lookback_days), int(volume_window_days)) - 1:
            continue
        row = rows[index]
        amount = _finite_float(row.amount)
        if amount is None or amount < max(0, float(min_amount)):
            continue
        current_close = _finite_float(row.close)
        lookback_row = rows[index - int(lookback_days)]
        lookback_close = _finite_float(lookback_row.close)
        if current_close is None or lookback_close is None or lookback_close <= 0:
            continue
        amount_window = [
            _finite_float(item.amount)
            for item in rows[index - int(volume_window_days) + 1:index + 1]
        ]
        amount_values = [value for value in amount_window if value is not None and value > 0]
        if not amount_values:
            continue
        average_amount = sum(amount_values) / len(amount_values)
        if average_amount <= 0:
            continue
        return_10 = (current_close / lookback_close - 1) * 100
        signal_score = return_10 * (amount / average_amount)
        lot_size = board_lots.get(symbol)
        if not lot_size:
            continue
        lot_value = current_close * lot_size
        budget_lots = int(single_budget / (lot_value * (1 + max(0, float(cost_rate))))) if lot_value > 0 else 0
        market_cap = _finite_float(target_meta.get(symbol, {}).get("market_cap")) or 0
        ranked_values.append((
            signal_score,
            HkConnectMomentumReviewCandidate(
                rank=0,
                market="HK",
                symbol=symbol,
                name=str(target_meta.get(symbol, {}).get("name") or symbol),
                signal_score=signal_score,
                return_10_percent=return_10,
                amount=amount,
                average_amount_20=average_amount,
                close=current_close,
                lot_size=int(lot_size),
                lot_value=lot_value,
                budget_lots=budget_lots,
                estimated_cash=budget_lots * lot_value * (1 + max(0, float(cost_rate))),
                market_cap=market_cap,
                selected=False,
            ),
        ))
    ranked_values.sort(key=lambda item: item[0], reverse=True)
    candidates: list[HkConnectMomentumReviewCandidate] = []
    selected: list[HkConnectMomentumReviewCandidate] = []
    for index, (_score, item) in enumerate(ranked_values[:20], start=1):
        is_selected = hsi_filter_passed and len(selected) < top_n_value and item.budget_lots > 0
        candidate = HkConnectMomentumReviewCandidate(
            rank=index,
            market=item.market,
            symbol=item.symbol,
            name=item.name,
            signal_score=item.signal_score,
            return_10_percent=item.return_10_percent,
            amount=item.amount,
            average_amount_20=item.average_amount_20,
            close=item.close,
            lot_size=item.lot_size,
            lot_value=item.lot_value,
            budget_lots=item.budget_lots,
            estimated_cash=item.estimated_cash,
            market_cap=item.market_cap,
            selected=is_selected,
        )
        candidates.append(candidate)
        if is_selected:
            selected.append(candidate)

    if not hsi_filter_passed:
        action = "hold_cash"
        summary = f"恒生指数 {hsi_close:.2f} 低于 60 日均线 {hsi_ma60:.2f}，策略空仓等待。"
    elif selected:
        action = "buy"
        names = "、".join(f"{item.symbol} {item.name} {item.budget_lots}手" for item in selected)
        summary = f"恒生指数站上 60 日线，下一交易日按开盘附近执行：{names}。"
    else:
        action = "wait"
        summary = "恒生指数过滤通过，但候选股在当前预算下无法整手买入，等待下一次信号。"

    return build_status(
        source="eastmoney:hk_connect_components; akshare:stock_hk_hist; akshare:index_hsi",
        status="done",
        signal_date=signal_date,
        hsi_date=hsi_date,
        hsi_close=hsi_close,
        hsi_ma60=hsi_ma60,
        hsi_filter_passed=hsi_filter_passed,
        action=action,
        summary=summary,
        pool_count=len(filtered_components),
        usable_count=len(rows_by_key),
        candidates=tuple(candidates),
        selected=tuple(selected),
    )


def _load_hk_connect_turnover_components(*, limit: int = 300) -> tuple[dict[str, Any], ...]:
    try:
        import requests
    except Exception:
        return ()
    url = "https://33.push2.eastmoney.com/api/qt/clist/get"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    }
    fields = "f12,f14,f2,f3,f5,f6,f8,f20,f21"
    result: list[dict[str, Any]] = []
    page_size = 100
    page_count = max(1, (max(1, int(limit)) + page_size - 1) // page_size)
    for page in range(1, page_count + 1):
        params = {
            "pn": page,
            "pz": page_size,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "fid": "f6",
            "fs": "b:DLMK0146,b:DLMK0144",
            "fields": fields,
        }
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
        except Exception:
            break
        data = response.json().get("data") or {}
        rows = data.get("diff") or ()
        for row in rows:
            symbol = _normalize_hk_symbol(row.get("f12"))
            if not symbol:
                continue
            result.append({
                "symbol": symbol,
                "name": str(row.get("f14") or symbol),
                "price": _finite_float(row.get("f2")),
                "change_percent": _finite_float(row.get("f3")),
                "volume": _finite_float(row.get("f5")),
                "amount": _finite_float(row.get("f6")),
                "turnover_rate": _finite_float(row.get("f8")),
                "market_cap": _finite_float(row.get("f20")),
                "float_market_cap": _finite_float(row.get("f21")),
            })
        if len(result) >= int(limit):
            break
    return tuple(result[: max(0, int(limit))])


def _review_start_date(end_date: str) -> str:
    try:
        end = dt.date.fromisoformat(str(end_date)[:10])
    except Exception:
        end = dt.date.today()
    return (end - dt.timedelta(days=900)).isoformat()


def _normalize_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _build_rotation_strategy_search_result(
    *,
    years: tuple[int, ...],
    limit: int | None,
    items: list[QlibRotationStrategySearchItem] | tuple[QlibRotationStrategySearchItem, ...],
    done_count: int,
    candidate_count: int,
    status: str,
    min_annual_return_percent: float = 5.0,
    require_beat_benchmark: bool = True,
    error: str = "",
) -> QlibRotationStrategySearchResult:
    ranked_items = sorted(
        items,
        key=lambda item: (
            item.is_qualified,
            item.beat_benchmark_year_count,
            item.profitable_year_count,
            item.min_excess_return_percent if item.min_excess_return_percent is not None else float("-inf"),
            item.min_return_percent if item.min_return_percent is not None else float("-inf"),
            item.average_return_percent if item.average_return_percent is not None else float("-inf"),
            item.total_profit,
        ),
        reverse=True,
    )
    return QlibRotationStrategySearchResult(
        pool=HK_POOL,
        source=f"{status}:{done_count}/{candidate_count}; cache:market_kline:ranked_rotation; hkex:board_lot",
        years=years,
        limit=limit,
        benchmark_name="恒生指数",
        items=tuple(ranked_items),
        min_annual_return_percent=float(min_annual_return_percent),
        require_beat_benchmark=bool(require_beat_benchmark),
        qualified_count=sum(1 for item in ranked_items if item.is_qualified),
        done_count=done_count,
        candidate_count=candidate_count,
        status=status,
        error=error,
    )


def _prepare_ranked_rotation_backtest_context(
    *,
    limit: int | None,
    end_date: str,
    score_profiles: tuple[str, ...],
    rank_metrics: tuple[str, ...],
    market_filters: tuple[str, ...],
    refresh: bool = False,
) -> QlibRankedRotationBacktestContext:
    normalized_profiles = tuple(dict.fromkeys(normalize_score_profile(value) for value in score_profiles)) or ("balanced",)
    normalized_rank_metrics = tuple(dict.fromkeys(str(value).lower() for value in rank_metrics))
    normalized_market_filters = tuple(dict.fromkeys(str(value).lower() for value in market_filters))
    needs_index = any(value in {"relative_momentum_60", "relative_reversal_60", "adaptive_value_breakout"} for value in normalized_rank_metrics) or any(
        value in {"hsi_ma60", "hsi_ma120", "hsi_ma200"} for value in normalized_market_filters
    )

    targets = list_hk_screen_targets(limit=limit)
    board_lots = load_hkex_board_lots(refresh=refresh)
    with connect_market_data_db() as conn:
        rows_by_target = _read_cached_daily_rows_for_targets_with_conn(conn, targets)

    row_maps: dict[tuple[str, str], dict[str, AkshareStockHistoryRow]] = {}
    rows_by_key: dict[tuple[str, str], tuple[AkshareStockHistoryRow, ...]] = {}
    previous_score_by_profile: dict[str, dict[tuple[str, str], dict[str, int | None]]] = {
        profile: {} for profile in normalized_profiles
    }
    previous_relative_momentum_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    previous_volume_breakout_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    previous_value_quality_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    skipped_count = 0

    index_rows = load_index_rows(market="HK", symbol="HSI", refresh=refresh) if needs_index else ()
    index_return_by_date = _index_period_return_by_date(rows=index_rows, periods=60) if any(
        value in {"relative_momentum_60", "relative_reversal_60", "adaptive_value_breakout"} for value in normalized_rank_metrics
    ) else {}
    market_allowed_by_filter = {
        filter_name: _market_allowed_by_date(rows=index_rows, filter_name=filter_name)
        for filter_name in normalized_market_filters
        if filter_name in {"hsi_ma60", "hsi_ma120", "hsi_ma200"}
    }

    for target in targets:
        key = (target.market, target.symbol)
        rows = tuple(row for row in rows_by_target.get(key, ()) if row.date <= end_date)
        if not rows or not board_lots.get(target.symbol):
            skipped_count += 1
            continue
        rows_by_key[key] = rows
        row_maps[key] = {row.date: row for row in rows}
        for profile in normalized_profiles:
            score_by_date = _daily_factor_scores(rows, score_profile=profile)
            previous_score_by_profile[profile][key] = _previous_value_by_date(rows, score_by_date)
        if any(value in {"relative_momentum_60", "relative_reversal_60"} for value in normalized_rank_metrics):
            relative_by_date = _relative_momentum_by_date(rows, index_return_by_date=index_return_by_date, periods=60)
            previous_relative_momentum_by_date[key] = _previous_value_by_date(rows, relative_by_date)
        if any(value in {"volume_breakout_rank", "adaptive_value_breakout"} for value in normalized_rank_metrics):
            breakout_by_date = _volume_breakout_rank_by_date(rows)
            previous_volume_breakout_by_date[key] = _previous_value_by_date(rows, breakout_by_date)
        if any(value in {"value_quality_rank", "value_score_rank", "adaptive_value_breakout"} for value in normalized_rank_metrics):
            value_quality_by_date = _value_quality_rank_by_date(rows, symbol=target.symbol, refresh=refresh)
            previous_value_quality_by_date[key] = _previous_value_by_date(rows, value_quality_by_date)

    return QlibRankedRotationBacktestContext(
        targets=targets,
        target_by_key={(target.market, target.symbol): target for target in targets},
        board_lots=board_lots,
        rows_by_key=rows_by_key,
        row_maps=row_maps,
        previous_score_by_profile=previous_score_by_profile,
        previous_relative_momentum_by_date=previous_relative_momentum_by_date,
        previous_volume_breakout_by_date=previous_volume_breakout_by_date,
        previous_value_quality_by_date=previous_value_quality_by_date,
        index_return_by_date=index_return_by_date,
        market_allowed_by_filter=market_allowed_by_filter,
        skipped_count=skipped_count,
    )


def backtest_hk_pool_ranked_rotation_strategy(
    *,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    score_profile: str = "balanced",
    rank_metric: str = "score",
    market_filter: str = "none",
    score_threshold: int = 0,
    min_amount: float = 0,
    top_n: int = 5,
    rebalance: str = "monthly",
    cost_rate: float = 0.01,
    refresh: bool = False,
    context: QlibRankedRotationBacktestContext | None = None,
    period_cache: _RankedRotationPeriodCache | None = None,
) -> QlibRankedRotationBacktestResult:
    normalized_profile = normalize_score_profile(score_profile)
    rank_metric_text = str(rank_metric).lower()
    normalized_rank_metric = (
        rank_metric_text
        if rank_metric_text
        in {
            "relative_momentum_60",
            "relative_reversal_60",
            "volume_breakout_rank",
            "value_quality_rank",
            "value_score_rank",
            "adaptive_value_breakout",
        }
        else "score"
    )
    market_filter_text = str(market_filter).lower()
    normalized_market_filter = market_filter_text if market_filter_text in {"hsi_ma60", "hsi_ma120", "hsi_ma200"} else "none"
    normalized_rebalance = str(rebalance).lower()
    if normalized_rebalance not in {"weekly", "monthly", "quarterly"}:
        normalized_rebalance = "monthly"
    if context is None:
        context = _prepare_ranked_rotation_backtest_context(
            limit=limit,
            end_date=end_date,
            score_profiles=(normalized_profile,),
            rank_metrics=(normalized_rank_metric,),
            market_filters=(normalized_market_filter,),
            refresh=refresh,
        )

    targets = context.targets
    target_by_key = context.target_by_key
    board_lots = context.board_lots
    row_maps = context.row_maps
    previous_score_by_date = context.previous_score_by_profile.get(normalized_profile, {})
    previous_relative_momentum_by_date = context.previous_relative_momentum_by_date
    previous_volume_breakout_by_date = context.previous_volume_breakout_by_date
    previous_value_quality_by_date = context.previous_value_quality_by_date
    index_return_by_date = context.index_return_by_date
    market_allowed_by_date = context.market_allowed_by_filter.get(normalized_market_filter, {})
    skipped_count = context.skipped_count
    all_dates = {
        row.date
        for rows in context.rows_by_key.values()
        for row in rows
        if start_date <= row.date <= end_date
    }

    ordered_dates = sorted(all_dates)
    if not ordered_dates:
        return QlibRankedRotationBacktestResult(
            pool=HK_POOL,
            source="cache:market_kline:ranked_rotation",
            start_date=start_date,
            end_date=end_date,
            score_profile=normalized_profile,
            rank_metric=normalized_rank_metric,
            market_filter=normalized_market_filter,
            score_threshold=int(score_threshold),
            top_n=max(1, int(top_n)),
            rebalance=normalized_rebalance,
            min_amount=max(0, float(min_amount)),
            cost_rate=max(0, float(cost_rate)),
            target_count=len(targets),
            tested_count=0,
            skipped_count=skipped_count,
            total_profit=0,
            return_percent=None,
            max_capital_used=0,
            total_fee=0,
            trade_count=0,
            closed_trade_count=0,
            benchmarks=load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh),
            error="没有可回测的日线数据",
        )

    positions: dict[tuple[str, str], QlibRankedRotationPosition] = {}
    cash = 0.0
    total_fee = 0.0
    trade_count = 0
    closed_trade_count = 0
    max_capital_used = 0.0
    last_rebalance_bucket = ""

    for current_date in ordered_dates:
        bucket = _rebalance_bucket(current_date, normalized_rebalance)
        should_rebalance = bucket != last_rebalance_bucket
        if should_rebalance:
            last_rebalance_bucket = bucket
            market_allowed = market_allowed_by_date.get(current_date, True)
            for key, position in list(positions.items()):
                row = row_maps.get(key, {}).get(current_date)
                close_or_open = _finite_float(row.open if row is not None else None) or _finite_float(row.close if row is not None else None)
                if close_or_open is None:
                    continue
                sell_gross = close_or_open * position.lot_size
                sell_fee = sell_gross * cost_rate
                cash += sell_gross - sell_fee
                total_fee += sell_fee
                trade_count += 1
                closed_trade_count += 1
                del positions[key]

            if not market_allowed:
                continue
            ranked: list[tuple[float, tuple[str, str], AkshareStockHistoryRow]] = []
            for key, row_map in row_maps.items():
                row = row_map.get(current_date)
                if row is None:
                    continue
                open_price = _finite_float(row.open)
                if open_price is None or open_price <= 0:
                    continue
                amount = _finite_float(row.amount)
                if amount is not None and amount < min_amount:
                    continue
                score = previous_score_by_date.get(key, {}).get(current_date)
                if score is None or score < score_threshold:
                    continue
                rank_value: float | None
                if normalized_rank_metric in {"relative_momentum_60", "relative_reversal_60"}:
                    rank_value = previous_relative_momentum_by_date.get(key, {}).get(current_date)
                    if normalized_rank_metric == "relative_reversal_60" and rank_value is not None:
                        rank_value = -rank_value
                elif normalized_rank_metric == "volume_breakout_rank":
                    rank_value = previous_volume_breakout_by_date.get(key, {}).get(current_date)
                elif normalized_rank_metric == "value_quality_rank":
                    rank_value = previous_value_quality_by_date.get(key, {}).get(current_date)
                elif normalized_rank_metric == "value_score_rank":
                    value_quality = previous_value_quality_by_date.get(key, {}).get(current_date)
                    rank_value = _value_score_rank_value(score=score, value_quality=value_quality)
                elif normalized_rank_metric == "adaptive_value_breakout":
                    index_return = index_return_by_date.get(current_date)
                    if index_return is not None and index_return > 0:
                        rank_value = previous_volume_breakout_by_date.get(key, {}).get(current_date)
                    else:
                        rank_value = previous_value_quality_by_date.get(key, {}).get(current_date)
                else:
                    rank_value = float(score)
                if rank_value is None:
                    continue
                ranked.append((float(rank_value), key, row))
            ranked.sort(key=lambda item: item[0], reverse=True)
            for rank_value, key, row in ranked[: max(1, int(top_n))]:
                target = target_by_key.get(key)
                if target is None:
                    continue
                lot_size = board_lots.get(target.symbol)
                open_price = _finite_float(row.open)
                if not lot_size or open_price is None:
                    continue
                buy_gross = open_price * lot_size
                buy_fee = buy_gross * cost_rate
                buy_cost = buy_gross + buy_fee
                cash -= buy_cost
                total_fee += buy_fee
                trade_count += 1
                positions[key] = QlibRankedRotationPosition(
                    target=target,
                    lot_size=lot_size,
                    buy_date=current_date,
                    buy_price=open_price,
                    buy_cost=buy_cost,
                    score=int(previous_score_by_date.get(key, {}).get(current_date) or round(rank_value)),
                )
        capital_used = sum(position.buy_cost for position in positions.values())
        max_capital_used = max(max_capital_used, capital_used)

    final_date = ordered_dates[-1]
    for key, position in list(positions.items()):
        row = row_maps.get(key, {}).get(final_date)
        final_price = _finite_float(row.close if row is not None else None)
        if final_price is None:
            continue
        sell_gross = final_price * position.lot_size
        sell_fee = sell_gross * cost_rate
        cash += sell_gross - sell_fee
        total_fee += sell_fee
        trade_count += 1
        closed_trade_count += 1
        del positions[key]

    benchmarks = load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh)
    return QlibRankedRotationBacktestResult(
        pool=HK_POOL,
        source="cache:market_kline:ranked_rotation; hkex:board_lot",
        start_date=ordered_dates[0],
        end_date=final_date,
        score_profile=normalized_profile,
        rank_metric=normalized_rank_metric,
        market_filter=normalized_market_filter,
        score_threshold=int(score_threshold),
        top_n=max(1, int(top_n)),
        rebalance=normalized_rebalance,
        min_amount=max(0, float(min_amount)),
        cost_rate=max(0, float(cost_rate)),
        target_count=len(targets),
        tested_count=len(row_maps),
        skipped_count=skipped_count,
        total_profit=cash,
        return_percent=cash / max_capital_used * 100 if max_capital_used else None,
        max_capital_used=max_capital_used,
        total_fee=total_fee,
        trade_count=trade_count,
        closed_trade_count=closed_trade_count,
        benchmarks=benchmarks,
    )


def backtest_hk_pool_ranked_rotation_equal_weight_strategy(
    *,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    score_profile: str = "balanced",
    rank_metric: str = "score",
    market_filter: str = "none",
    score_threshold: int = 0,
    min_amount: float = 0,
    top_n: int = 5,
    rebalance: str = "monthly",
    cost_rate: float = 0.01,
    refresh: bool = False,
    context: QlibRankedRotationBacktestContext | None = None,
    period_cache: _RankedRotationPeriodCache | None = None,
) -> QlibRankedRotationBacktestResult:
    normalized_profile = normalize_score_profile(score_profile)
    rank_metric_text = str(rank_metric).lower()
    normalized_rank_metric = (
        rank_metric_text
        if rank_metric_text
        in {
            "relative_momentum_60",
            "relative_reversal_60",
            "volume_breakout_rank",
            "value_quality_rank",
            "value_score_rank",
            "adaptive_value_breakout",
        }
        else "score"
    )
    market_filter_text = str(market_filter).lower()
    normalized_market_filter = market_filter_text if market_filter_text in {"hsi_ma60", "hsi_ma120", "hsi_ma200"} else "none"
    normalized_rebalance = str(rebalance).lower()
    if normalized_rebalance not in {"weekly", "monthly", "quarterly"}:
        normalized_rebalance = "monthly"
    if context is None:
        context = _prepare_ranked_rotation_backtest_context(
            limit=limit,
            end_date=end_date,
            score_profiles=(normalized_profile,),
            rank_metrics=(normalized_rank_metric,),
            market_filters=(normalized_market_filter,),
            refresh=refresh,
        )

    all_dates = sorted(
        {
            row.date
            for rows in context.rows_by_key.values()
            for row in rows
            if start_date <= row.date <= end_date
        }
    )
    if not all_dates:
        return QlibRankedRotationBacktestResult(
            pool=HK_POOL,
            source="cache:market_kline:ranked_rotation_equal_weight",
            start_date=start_date,
            end_date=end_date,
            score_profile=normalized_profile,
            rank_metric=normalized_rank_metric,
            market_filter=normalized_market_filter,
            score_threshold=int(score_threshold),
            top_n=max(1, int(top_n)),
            rebalance=normalized_rebalance,
            min_amount=max(0, float(min_amount)),
            cost_rate=max(0, float(cost_rate)),
            target_count=len(context.targets),
            tested_count=0,
            skipped_count=context.skipped_count,
            total_profit=0,
            return_percent=None,
            max_capital_used=0,
            total_fee=0,
            trade_count=0,
            closed_trade_count=0,
            benchmarks=load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh),
            error="没有可回测的日线数据",
        )

    market_allowed_by_date = context.market_allowed_by_filter.get(normalized_market_filter, {})
    rebalance_dates: list[str] = []
    last_bucket = ""
    for current_date in all_dates:
        bucket = _rebalance_bucket(current_date, normalized_rebalance)
        if bucket != last_bucket:
            last_bucket = bucket
            rebalance_dates.append(current_date)

    capital = 1.0
    total_fee = 0.0
    trade_count = 0
    top_n_value = max(1, int(top_n))
    cache = period_cache or _RankedRotationPeriodCache(context)
    for index, current_date in enumerate(rebalance_dates):
        next_date = rebalance_dates[index + 1] if index + 1 < len(rebalance_dates) else end_date
        if not market_allowed_by_date.get(current_date, True):
            continue
        selected_returns = cache.selected_returns(
            current_date=current_date,
            next_date=next_date,
            score_profile=normalized_profile,
            rank_metric=normalized_rank_metric,
            score_threshold=int(score_threshold),
            min_amount=max(0, float(min_amount)),
            top_n=top_n_value,
        )
        if not selected_returns:
            continue
        gross_period_return = sum(selected_returns) / len(selected_returns)
        before_cost_multiplier = 1 + gross_period_return / 100
        after_cost_multiplier = before_cost_multiplier * (1 - max(0, float(cost_rate))) / (1 + max(0, float(cost_rate)))
        next_capital = capital * after_cost_multiplier
        total_fee += max(0, capital * before_cost_multiplier - next_capital)
        capital = next_capital
        trade_count += len(selected_returns) * 2

    benchmarks = load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh)
    return QlibRankedRotationBacktestResult(
        pool=HK_POOL,
        source="cache:market_kline:ranked_rotation_equal_weight",
        start_date=all_dates[0],
        end_date=all_dates[-1],
        score_profile=normalized_profile,
        rank_metric=normalized_rank_metric,
        market_filter=normalized_market_filter,
        score_threshold=int(score_threshold),
        top_n=top_n_value,
        rebalance=normalized_rebalance,
        min_amount=max(0, float(min_amount)),
        cost_rate=max(0, float(cost_rate)),
        target_count=len(context.targets),
        tested_count=len(context.row_maps),
        skipped_count=context.skipped_count,
        total_profit=capital - 1,
        return_percent=(capital - 1) * 100,
        max_capital_used=1,
        total_fee=total_fee,
        trade_count=trade_count,
        closed_trade_count=trade_count,
        benchmarks=benchmarks,
    )


def backtest_hk_pool_hsi20_breakout_reversal_equal_weight_strategy(
    *,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    score_profile: str = "balanced",
    min_amount: float = 10_000_000,
    cost_rate: float = 0.005,
    refresh: bool = False,
    context: QlibRankedRotationBacktestContext | None = None,
    period_cache: _RankedRotationPeriodCache | None = None,
) -> QlibRankedRotationBacktestResult:
    normalized_profile = normalize_score_profile(score_profile)
    if context is None:
        context = _prepare_ranked_rotation_backtest_context(
            limit=limit,
            end_date=end_date,
            score_profiles=(normalized_profile,),
            rank_metrics=("volume_breakout_rank", "relative_reversal_60"),
            market_filters=("hsi_ma60",),
            refresh=refresh,
        )
    cache = period_cache or _RankedRotationPeriodCache(context)
    index_rows = load_index_rows(market="HK", symbol="HSI", refresh=refresh)
    index_return_20_by_date = _index_period_return_by_date(rows=index_rows, periods=20)
    all_dates = sorted(
        {
            row.date
            for rows in context.rows_by_key.values()
            for row in rows
            if start_date <= row.date <= end_date
        }
    )
    if not all_dates:
        return QlibRankedRotationBacktestResult(
            pool=HK_POOL,
            source="cache:market_kline:hsi20_breakout_reversal_equal_weight",
            start_date=start_date,
            end_date=end_date,
            score_profile=normalized_profile,
            rank_metric="hsi20_breakout5_else_reversal1",
            market_filter="hsi_ma60",
            score_threshold=0,
            top_n=5,
            rebalance="monthly",
            min_amount=max(0, float(min_amount)),
            cost_rate=max(0, float(cost_rate)),
            target_count=len(context.targets),
            tested_count=0,
            skipped_count=context.skipped_count,
            total_profit=0,
            return_percent=None,
            max_capital_used=0,
            total_fee=0,
            trade_count=0,
            closed_trade_count=0,
            benchmarks=load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh),
            error="没有可回测的日线数据",
        )

    rebalance_dates: list[str] = []
    last_bucket = ""
    for current_date in all_dates:
        bucket = _rebalance_bucket(current_date, "monthly")
        if bucket != last_bucket:
            last_bucket = bucket
            rebalance_dates.append(current_date)

    capital = 1.0
    total_fee = 0.0
    trade_count = 0
    market_allowed_by_date = context.market_allowed_by_filter.get("hsi_ma60", {})
    for index, current_date in enumerate(rebalance_dates):
        next_date = rebalance_dates[index + 1] if index + 1 < len(rebalance_dates) else end_date
        if not market_allowed_by_date.get(current_date, True):
            continue
        index_return_20 = index_return_20_by_date.get(current_date)
        if index_return_20 is not None and 5 <= index_return_20 <= 10:
            rank_metric = "volume_breakout_rank"
            score_threshold = 0
            top_n = 5
        else:
            rank_metric = "relative_reversal_60"
            score_threshold = 70
            top_n = 1
        selected_returns = cache.selected_returns(
            current_date=current_date,
            next_date=next_date,
            score_profile=normalized_profile,
            rank_metric=rank_metric,
            score_threshold=score_threshold,
            min_amount=max(0, float(min_amount)),
            top_n=top_n,
        )
        if not selected_returns:
            continue
        gross_period_return = sum(selected_returns) / len(selected_returns)
        before_cost_multiplier = 1 + gross_period_return / 100
        after_cost_multiplier = before_cost_multiplier * (1 - max(0, float(cost_rate))) / (1 + max(0, float(cost_rate)))
        next_capital = capital * after_cost_multiplier
        total_fee += max(0, capital * before_cost_multiplier - next_capital)
        capital = next_capital
        trade_count += len(selected_returns) * 2

    benchmarks = load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh)
    return QlibRankedRotationBacktestResult(
        pool=HK_POOL,
        source="cache:market_kline:hsi20_breakout_reversal_equal_weight",
        start_date=all_dates[0],
        end_date=all_dates[-1],
        score_profile=normalized_profile,
        rank_metric="hsi20_breakout5_else_reversal1",
        market_filter="hsi_ma60",
        score_threshold=0,
        top_n=5,
        rebalance="monthly",
        min_amount=max(0, float(min_amount)),
        cost_rate=max(0, float(cost_rate)),
        target_count=len(context.targets),
        tested_count=len(context.row_maps),
        skipped_count=context.skipped_count,
        total_profit=capital - 1,
        return_percent=(capital - 1) * 100,
        max_capital_used=1,
        total_fee=total_fee,
        trade_count=trade_count,
        closed_trade_count=trade_count,
        benchmarks=benchmarks,
    )


def _ranked_rotation_period_selected_returns(
    *,
    context: QlibRankedRotationBacktestContext,
    current_date: str,
    next_date: str,
    score_profile: str,
    rank_metric: str,
    score_threshold: int,
    min_amount: float,
    top_n: int,
) -> list[float]:
    return _RankedRotationPeriodCache(context).selected_returns(
        current_date=current_date,
        next_date=next_date,
        score_profile=score_profile,
        rank_metric=rank_metric,
        score_threshold=score_threshold,
        min_amount=min_amount,
        top_n=top_n,
    )


def _ranked_rotation_rank_value(
    *,
    context: QlibRankedRotationBacktestContext,
    key: tuple[str, str],
    current_date: str,
    score: int | float,
    rank_metric: str,
) -> float | None:
    if rank_metric in {"relative_momentum_60", "relative_reversal_60"}:
        rank_value = context.previous_relative_momentum_by_date.get(key, {}).get(current_date)
        if rank_metric == "relative_reversal_60" and rank_value is not None:
            rank_value = -rank_value
        return rank_value
    if rank_metric == "volume_breakout_rank":
        return context.previous_volume_breakout_by_date.get(key, {}).get(current_date)
    if rank_metric == "value_quality_rank":
        return context.previous_value_quality_by_date.get(key, {}).get(current_date)
    if rank_metric == "value_score_rank":
        value_quality = context.previous_value_quality_by_date.get(key, {}).get(current_date)
        return _value_score_rank_value(score=score, value_quality=value_quality)
    if rank_metric == "adaptive_value_breakout":
        index_return = context.index_return_by_date.get(current_date)
        if index_return is not None and index_return > 0:
            return context.previous_volume_breakout_by_date.get(key, {}).get(current_date)
        return context.previous_value_quality_by_date.get(key, {}).get(current_date)
    return float(score)


def _previous_value_by_date(
    rows: tuple[AkshareStockHistoryRow, ...],
    values_by_date: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    previous: Any = None
    for row in rows:
        result[row.date] = previous
        value = values_by_date.get(row.date)
        if value is not None:
            previous = value
    return result


def _index_period_return_by_date(*, rows: tuple[dict[str, Any], ...], periods: int) -> dict[str, float | None]:
    closes: list[float] = []
    result: dict[str, float | None] = {}
    for row in rows:
        close = _finite_float(row.get("close"))
        date = str(row.get("date") or "")
        if not date or close is None:
            continue
        closes.append(close)
        result[date] = _period_return_from_values(closes, periods)
    return result


def _market_allowed_by_date(*, rows: tuple[dict[str, Any], ...], filter_name: str) -> dict[str, bool]:
    if filter_name == "none":
        return {}
    closes: list[float] = []
    raw: dict[str, bool] = {}
    for row in rows:
        close = _finite_float(row.get("close"))
        date = str(row.get("date") or "")
        if not date or close is None:
            continue
        closes.append(close)
        if filter_name in {"hsi_ma60", "hsi_ma120", "hsi_ma200"}:
            periods = int(filter_name.replace("hsi_ma", ""))
            ma = sum(closes[-periods:]) / periods if len(closes) >= periods else None
            raw[date] = bool(ma is not None and close >= ma)
    shifted: dict[str, bool] = {}
    previous = False
    for row in rows:
        date = str(row.get("date") or "")
        if not date:
            continue
        shifted[date] = previous
        previous = raw.get(date, previous)
    return shifted


def _relative_momentum_by_date(
    rows: tuple[AkshareStockHistoryRow, ...],
    *,
    index_return_by_date: dict[str, float | None],
    periods: int,
) -> dict[str, float | None]:
    closes: list[float] = []
    result: dict[str, float | None] = {}
    for row in rows:
        close = _finite_float(row.close)
        if close is None:
            result[row.date] = None
            continue
        closes.append(close)
        stock_return = _period_return_from_values(closes, periods)
        index_return = index_return_by_date.get(row.date)
        result[row.date] = stock_return - index_return if stock_return is not None and index_return is not None else None
    return result


def _volume_breakout_rank_by_date(rows: tuple[AkshareStockHistoryRow, ...]) -> dict[str, float | None]:
    closes: list[float] = []
    volumes: list[float] = []
    result: dict[str, float | None] = {}
    for row in rows:
        close = _finite_float(row.close)
        volume = _finite_float(row.volume)
        if close is None:
            result[row.date] = None
            continue
        closes.append(close)
        volumes.append(volume or 0)
        return_5 = _period_return_from_values(closes, 5)
        return_20 = _period_return_from_values(closes, 20)
        if return_5 is None or return_20 is None or len(volumes) < 20:
            result[row.date] = None
            continue
        avg_volume_5 = sum(volumes[-5:]) / 5
        avg_volume_20 = sum(volumes[-20:]) / 20
        volume_ratio = avg_volume_5 / avg_volume_20 if avg_volume_20 else None
        ma_20 = sum(closes[-20:]) / 20
        ma_20_distance = (close / ma_20 - 1) * 100 if ma_20 else 0
        score = return_20 + return_5 * 0.7
        if volume_ratio is not None:
            score += max(0, volume_ratio - 1) * 12
        if ma_20_distance > 25:
            score -= (ma_20_distance - 25) * 0.8
        if return_5 > 40:
            score -= (return_5 - 40) * 0.5
        result[row.date] = score
    return result


def _value_quality_rank_by_date(
    rows: tuple[AkshareStockHistoryRow, ...],
    *,
    symbol: str,
    refresh: bool = False,
) -> dict[str, float | None]:
    fundamentals = _load_hk_value_quality_factors(symbol=symbol, refresh=refresh)
    pe_by_date = fundamentals.get("pe") or {}
    pb_by_date = fundamentals.get("pb") or {}
    roe_by_year = fundamentals.get("roe") or {}
    pe_cursor = _dated_factor_cursor(pe_by_date)
    pb_cursor = _dated_factor_cursor(pb_by_date)
    result: dict[str, float | None] = {}
    for row in rows:
        row_date = _parse_iso_date(row.date)
        pe = _latest_dated_factor_value(pe_cursor, row_date, max_stale_days=VALUE_QUALITY_MAX_STALE_DAYS)
        pb = _latest_dated_factor_value(pb_cursor, row_date, max_stale_days=VALUE_QUALITY_MAX_STALE_DAYS)
        fiscal_year = int(row.date[:4]) - 2 if len(row.date) >= 4 and row.date[:4].isdigit() else None
        roe = _latest_factor_year_value(roe_by_year, fiscal_year)
        result[row.date] = _value_quality_rank_score(pe=pe, pb=pb, roe=roe)
    return result


def _load_hk_value_quality_factors(*, symbol: str, refresh: bool = False) -> dict[str, dict[str, float]]:
    if not refresh:
        return _load_cached_hk_value_quality_factors(symbol)
    return _load_uncached_hk_value_quality_factors(symbol=symbol, refresh=True)


@lru_cache(maxsize=4096)
def _load_cached_hk_value_quality_factors(symbol: str) -> dict[str, dict[str, float]]:
    return _load_uncached_hk_value_quality_factors(symbol=symbol, refresh=False)


def _load_uncached_hk_value_quality_factors(*, symbol: str, refresh: bool = False) -> dict[str, dict[str, float]]:
    cache_path = get_settings().data_dir / "stock" / "fundamentals" / "hk_value_quality" / f"{symbol}.json"
    if not refresh:
        cached = _read_hk_value_quality_factor_cache(cache_path)
        if cached and _has_recent_hk_value_quality_valuation(cached):
            return cached
    try:
        import akshare as ak

        pe_rows = _fetch_baidu_valuation_by_date(ak, symbol=symbol, indicator="市盈率(TTM)")
        pb_rows = _fetch_baidu_valuation_by_date(ak, symbol=symbol, indicator="市净率")
        roe_rows = _fetch_em_roe_by_year(ak, symbol=symbol)
        data = {
            "pe": pe_rows,
            "pb": pb_rows,
            "roe": roe_rows,
        }
        _write_hk_value_quality_factor_cache(cache_path, data)
        return data
    except Exception:
        cached = _read_hk_value_quality_factor_cache(cache_path)
        return cached or {"pe": {}, "pb": {}, "roe": {}}


def _fetch_baidu_valuation_by_date(ak_module, *, symbol: str, indicator: str) -> dict[str, float]:
    frame = ak_module.stock_hk_valuation_baidu(symbol=symbol, indicator=indicator, period="全部")
    result: dict[str, float] = {}
    for row in frame.to_dict("records"):
        date = str(row.get("date") or "")[:10]
        value = _finite_float(row.get("value"))
        if date and value is not None:
            result[date] = value
    return result


def _fetch_em_roe_by_year(ak_module, *, symbol: str) -> dict[str, float]:
    frame = ak_module.stock_financial_hk_analysis_indicator_em(symbol=symbol, indicator="年度")
    result: dict[str, float] = {}
    for row in frame.to_dict("records"):
        year = str(row.get("REPORT_DATE") or "")[:4]
        value = _finite_float(row.get("ROE_YEARLY"))
        if year.isdigit() and value is not None:
            result[year] = value
    return result


def _read_hk_value_quality_factor_cache(path) -> dict[str, dict[str, float]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, dict[str, float]] = {}
    for key in ("pe", "pb", "roe"):
        values = payload.get(key)
        if isinstance(values, dict):
            result[key] = {
                str(item_key): float(item_value)
                for item_key, item_value in values.items()
                if _finite_float(item_value) is not None
            }
        else:
            result[key] = {}
    return result


def _write_hk_value_quality_factor_cache(path, data: dict[str, dict[str, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _has_recent_hk_value_quality_valuation(data: dict[str, dict[str, float]]) -> bool:
    latest_dates: list[dt.date] = []
    for key in ("pe", "pb"):
        dated_values = _dated_factor_cursor(data.get(key) or {})
        if dated_values:
            latest_dates.append(dated_values[-1][0])
    if not latest_dates:
        return False
    return max(latest_dates) >= dt.date.today() - dt.timedelta(days=450)


def _dated_factor_cursor(values_by_date: dict[str, float]) -> tuple[tuple[dt.date, float], ...]:
    values: list[tuple[dt.date, float]] = []
    for date_text, value in values_by_date.items():
        date_value = _parse_iso_date(str(date_text))
        factor_value = _finite_float(value)
        if date_value is not None and factor_value is not None:
            values.append((date_value, factor_value))
    return tuple(sorted(values, key=lambda item: item[0]))


def _latest_dated_factor_value(
    dated_values: tuple[tuple[dt.date, float], ...],
    current_date: dt.date | None,
    *,
    max_stale_days: int,
) -> float | None:
    if current_date is None or not dated_values:
        return None
    latest_value: float | None = None
    latest_date: dt.date | None = None
    for date_value, factor_value in dated_values:
        if date_value > current_date:
            break
        latest_date = date_value
        latest_value = factor_value
    if latest_date is None or latest_value is None:
        return None
    if (current_date - latest_date).days > max_stale_days:
        return None
    return latest_value


def _parse_iso_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except Exception:
        return None


def _latest_factor_year_value(values_by_year: dict[str, float], max_year: int | None) -> float | None:
    if max_year is None:
        return None
    candidates = [
        (int(year), value)
        for year, value in values_by_year.items()
        if str(year).isdigit() and int(year) <= max_year
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _value_quality_rank_score(*, pe: float | None, pb: float | None, roe: float | None) -> float | None:
    if pe is None and pb is None and roe is None:
        return None
    score = 0.0
    if pe is not None:
        if 0 < pe <= 35:
            score += (35 - pe) / 35 * 40
        elif pe <= 0:
            score -= 12
    if pb is not None:
        if 0 < pb <= 5:
            score += (5 - pb) / 5 * 30
        elif pb <= 0:
            score -= 8
    if roe is not None:
        score += max(0, min(30, (roe + 5) / 30 * 30))
    return score


def _value_score_rank_value(*, score: int | float | None, value_quality: float | None) -> float | None:
    score_value = _finite_float(score)
    value_quality_value = _finite_float(value_quality)
    if score_value is None or value_quality_value is None:
        return None
    return value_quality_value * 0.55 + score_value * 0.45


def _period_return_from_values(values: list[float], periods: int) -> float | None:
    if len(values) <= periods:
        return None
    base = values[-periods - 1]
    latest = values[-1]
    return (latest / base - 1) * 100 if base else None


def _rebalance_bucket(date_text: str, rebalance: str) -> str:
    if rebalance == "weekly":
        date = dt.date.fromisoformat(date_text[:10])
        year, week, _day = date.isocalendar()
        return f"{year}-W{week:02d}"
    if rebalance == "quarterly":
        year = date_text[:4]
        month = int(date_text[5:7])
        quarter = (month - 1) // 3 + 1
        return f"{year}-Q{quarter}"
    return date_text[:7]


def backtest_hk_pool_one_lot_score(
    *,
    refresh: bool = False,
    limit: int | None = None,
    start_date: str = "2025-01-01",
    end_date: str | None = None,
    score_threshold: int = 84,
    score_profile: str = "balanced",
    take_profit_percent: float = 5.0,
    stop_loss_percent: float = 0.0,
    max_holding_days: int = 0,
    cost_rate: float = 0.01,
    force_liquidate_end: bool = True,
    progress_callback: Callable[[QlibPoolBacktestResult], None] | None = None,
) -> QlibPoolBacktestResult:
    legacy_cache_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_backtest_one_lot_score.json"
    cache_key = {
        "start_date": start_date,
        "end_date": end_date or dt.date.today().isoformat(),
        "score_threshold": int(score_threshold),
        "score_profile": normalize_score_profile(score_profile),
        "take_profit_percent": float(take_profit_percent),
        "stop_loss_percent": float(stop_loss_percent),
        "max_holding_days": max(0, int(max_holding_days)),
        "cost_rate": float(cost_rate),
        "force_liquidate_end": bool(force_liquidate_end),
        "limit": limit,
    }
    cache_path = _hk_pool_backtest_cache_path(cache_key)
    if not refresh:
        cached = _read_hk_pool_backtest_cache(cache_path, cache_key) or _read_hk_pool_backtest_cache(legacy_cache_path, cache_key)
        if cached is not None:
            return cached

    targets = list_hk_screen_targets(limit=limit)
    board_lots = load_hkex_board_lots(refresh=refresh)
    items: list[QlibPoolBacktestItem] = []
    done_count = 0
    for target_chunk in _chunk_targets_by_size(targets, 100):
        items.extend(_backtest_hk_pool_target_chunk(
            targets=target_chunk,
            board_lots=board_lots,
            start_date=start_date,
            end_date=end_date,
            score_threshold=score_threshold,
            score_profile=score_profile,
            take_profit_percent=take_profit_percent,
            stop_loss_percent=stop_loss_percent,
            max_holding_days=max_holding_days,
            cost_rate=cost_rate,
            force_liquidate_end=force_liquidate_end,
        ))
        done_count += len(target_chunk)
        if progress_callback is not None:
            progress_callback(_build_hk_pool_backtest_result(
                targets=targets,
                items=items,
                source=f"running:{done_count}/{len(targets)}; cache:market_kline:batched; hkex:board_lot",
                start_date=start_date,
                end_date=end_date,
                score_threshold=score_threshold,
                score_profile=score_profile,
                take_profit_percent=take_profit_percent,
                stop_loss_percent=stop_loss_percent,
                max_holding_days=max_holding_days,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
                refresh=refresh,
            ))

    ranked_items = sorted(
        items,
        key=lambda item: (
            item.result is not None,
            item.result.total_profit if item.result is not None else float("-inf"),
            item.result.trade_count if item.result is not None else -1,
        ),
        reverse=True,
    )
    result = _build_hk_pool_backtest_result(
        targets=targets,
        items=ranked_items,
        source="cache:market_kline:batched; hkex:board_lot",
        start_date=start_date,
        end_date=end_date,
        score_threshold=score_threshold,
        score_profile=score_profile,
        take_profit_percent=take_profit_percent,
        stop_loss_percent=stop_loss_percent,
        max_holding_days=max_holding_days,
        cost_rate=cost_rate,
        force_liquidate_end=force_liquidate_end,
        refresh=refresh,
    )
    _write_hk_pool_backtest_cache(cache_path, cache_key, result)
    if progress_callback is not None:
        progress_callback(result)
    return result


def _build_hk_pool_backtest_result(
    *,
    targets: tuple[QlibScreenTarget, ...],
    items: list[QlibPoolBacktestItem] | tuple[QlibPoolBacktestItem, ...],
    source: str,
    start_date: str,
    end_date: str | None,
    score_threshold: int,
    score_profile: str,
    take_profit_percent: float,
    stop_loss_percent: float,
    max_holding_days: int,
    cost_rate: float,
    force_liquidate_end: bool,
    refresh: bool,
) -> QlibPoolBacktestResult:
    ranked_items = sorted(
        items,
        key=lambda item: (
            item.result is not None,
            item.result.total_profit if item.result is not None else float("-inf"),
            item.result.trade_count if item.result is not None else -1,
        ),
        reverse=True,
    )
    return QlibPoolBacktestResult(
        pool=HK_POOL,
        source=source,
        target_count=len(targets),
        tested_count=sum(1 for item in items if item.result is not None and not item.error and item.result.points),
        skipped_count=sum(1 for item in items if item.result is None or bool(item.error)),
        start_date=start_date,
        end_date=end_date or dt.date.today().isoformat(),
        score_threshold=int(score_threshold),
        score_profile=normalize_score_profile(score_profile),
        take_profit_percent=float(take_profit_percent),
        stop_loss_percent=max(0, float(stop_loss_percent)),
        max_holding_days=max(0, int(max_holding_days)),
        cost_rate=float(cost_rate),
        total_profit=sum(item.result.total_profit for item in items if item.result is not None and not item.error),
        total_invested=sum(item.result.total_invested for item in items if item.result is not None and not item.error),
        total_fee=sum(item.result.total_fee for item in items if item.result is not None and not item.error),
        max_capital_used=sum(item.result.max_capital_used for item in items if item.result is not None and not item.error),
        trade_count=sum(item.result.trade_count for item in items if item.result is not None and not item.error),
        closed_trade_count=sum(item.result.closed_trade_count for item in items if item.result is not None and not item.error),
        open_position_count=sum(1 for item in items if item.result is not None and not item.error and item.result.open_position_shares > 0),
        benchmarks=load_index_benchmarks(start_date=start_date, end_date=end_date, refresh=refresh),
        items=tuple(ranked_items),
        force_liquidate_end=force_liquidate_end,
    )


def _chunk_targets_by_size(targets: tuple[QlibScreenTarget, ...], chunk_size: int) -> tuple[tuple[QlibScreenTarget, ...], ...]:
    if chunk_size <= 0:
        return (targets,)
    return tuple(targets[index:index + chunk_size] for index in range(0, len(targets), chunk_size))


def _backtest_hk_pool_target_chunk(
    *,
    targets: tuple[QlibScreenTarget, ...],
    board_lots: dict[str, int],
    start_date: str,
    end_date: str | None,
    score_threshold: int,
    score_profile: str,
    take_profit_percent: float,
    stop_loss_percent: float,
    max_holding_days: int,
    cost_rate: float,
    force_liquidate_end: bool,
) -> list[QlibPoolBacktestItem]:
    items: list[QlibPoolBacktestItem] = []
    with connect_market_data_db() as conn:
        rows_by_target = _read_cached_daily_rows_for_targets_with_conn(conn, targets)
        for target in targets:
            lot_size = board_lots.get(target.symbol)
            if not lot_size:
                items.append(QlibPoolBacktestItem(target=target, lot_size=None, result=None, error="缺少港交所一手股数字段"))
                continue
            try:
                rows = rows_by_target.get((target.market, target.symbol), ())
                result = backtest_qlib_rows_one_lot_score_strategy(
                    target=target.qlib_target,
                    all_rows=rows,
                    source="cache",
                    start_date=start_date,
                    end_date=end_date,
                    lot_size=lot_size,
                    score_threshold=score_threshold,
                    score_profile=score_profile,
                    take_profit_percent=take_profit_percent,
                    stop_loss_percent=stop_loss_percent,
                    max_holding_days=max_holding_days,
                    cost_rate=cost_rate,
                    force_liquidate_end=force_liquidate_end,
                )
            except Exception as exc:
                items.append(QlibPoolBacktestItem(target=target, lot_size=lot_size, result=None, error=str(exc)))
                continue
            if result.error or not result.points:
                items.append(QlibPoolBacktestItem(target=target, lot_size=lot_size, result=result, error=result.error or "本地日线数据不足"))
                continue
            items.append(QlibPoolBacktestItem(target=target, lot_size=lot_size, result=result, error=result.error))
    return items


def screen_hk_pool(
    *,
    refresh: bool = False,
    limit: int | None = None,
    start_date: str = QLIB_EXPORT_START_DATE,
) -> QlibScreenResult:
    if not refresh:
        cached_result = _read_hk_score_cache(limit=limit)
        if cached_result is not None:
            return cached_result

    rows, source = _load_hk_pool_rows(refresh=refresh)
    targets = [
        QlibScreenTarget(market="HK", symbol=_normalize_hk_symbol(row.get("代码")), name=str(row.get("名称") or row.get("中文名称") or ""), pool=HK_POOL, start_date=start_date)
        for row in rows
    ]
    targets = [target for target in targets if target.symbol]
    unique_targets: dict[str, QlibScreenTarget] = {}
    for target in targets:
        unique_targets.setdefault(target.symbol, target)
    ordered_targets = sorted(unique_targets.values(), key=lambda item: item.symbol)
    if limit is not None:
        ordered_targets = ordered_targets[: max(0, int(limit))]

    items: list[QlibScreenItem] = []
    failed_count = 0
    for target in ordered_targets:
        analysis = _screen_one_target(target, refresh=refresh)
        if analysis.row_count <= 0:
            failed_count += 1
        items.append(QlibScreenItem(target=target, analysis=analysis))

    ranked_items = sorted(
        items,
        key=lambda item: (
            item.analysis.score is not None,
            item.analysis.score if item.analysis.score is not None else -1,
            item.analysis.row_count,
        ),
        reverse=True,
    )
    return QlibScreenResult(
        pool=HK_POOL,
        source=source,
        target_count=len(ordered_targets),
        analyzed_count=sum(1 for item in items if item.analysis.row_count > 0),
        failed_count=failed_count,
        items=tuple(ranked_items),
    )


def _read_hk_score_cache(*, limit: int | None = None) -> QlibScreenResult | None:
    score_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_scores.json"
    progress_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_sync_progress.json"
    try:
        raw_items = json.loads(score_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw_items, list) or not raw_items:
        return None

    progress: dict[str, Any] = {}
    try:
        loaded_progress = json.loads(progress_path.read_text(encoding="utf-8"))
        if isinstance(loaded_progress, dict):
            progress = loaded_progress
    except Exception:
        progress = {}
    progress = _normalize_hk_score_progress(progress)

    parsed_items = [
        _screen_item_from_cached_score(row)
        for row in raw_items
        if isinstance(row, dict)
    ]
    parsed_items = [item for item in parsed_items if item is not None]
    if limit is not None:
        parsed_items = parsed_items[: max(0, int(limit))]
    return QlibScreenResult(
        pool=HK_POOL,
        source=f"cache:hk_pool_scores:{progress.get('status') or 'unknown'}:{progress.get('done', len(raw_items))}/{progress.get('total', len(raw_items))}",
        target_count=int(progress.get("total") or len(raw_items)),
        analyzed_count=sum(1 for item in parsed_items if item.analysis.row_count > 0),
        failed_count=sum(1 for item in parsed_items if item.analysis.row_count <= 0),
        items=tuple(parsed_items),
    )


def _normalize_hk_score_progress(progress: dict[str, Any]) -> dict[str, Any]:
    if progress.get("status") != "running":
        return progress
    updated_at = str(progress.get("updated_at") or "")
    try:
        updated_time = dt.datetime.fromisoformat(updated_at)
    except Exception:
        updated_time = None
    if updated_time is None or dt.datetime.now() - updated_time > dt.timedelta(minutes=30):
        normalized = dict(progress)
        normalized["status"] = "stalled"
        normalized["error"] = normalized.get("error") or "后台评分进度超过 30 分钟未更新，任务可能已被后端重启或数据库锁中断"
        return normalized
    return progress


def _screen_item_from_cached_score(row: dict[str, Any]) -> QlibScreenItem | None:
    symbol = _normalize_hk_symbol(row.get("symbol"))
    if not symbol:
        return None
    target = QlibScreenTarget(
        market=str(row.get("market") or "HK"),
        symbol=symbol,
        name=str(row.get("name") or symbol),
        pool=str(row.get("pool") or HK_POOL),
    )
    analysis = QlibFactorAnalysis(
        market=target.market,
        symbol=target.symbol,
        name=target.name,
        qlib_symbol=f"{target.market}{target.symbol}".lower(),
        row_count=int(row.get("row_count") or 0),
        source=str(row.get("source") or "cache"),
        start_date=str(row.get("start_date") or ""),
        end_date=str(row.get("end_date") or ""),
        latest_close=_float_or_none(row.get("latest_close")),
        latest_change_percent=_float_or_none(row.get("latest_change_percent")),
        return_5=_float_or_none(row.get("return_5")),
        return_20=_float_or_none(row.get("return_20")),
        return_60=_float_or_none(row.get("return_60")),
        ma_5=_float_or_none(row.get("ma_5")),
        ma_20=_float_or_none(row.get("ma_20")),
        ma_60=_float_or_none(row.get("ma_60")),
        ma_20_distance=_float_or_none(row.get("ma_20_distance")),
        volatility_20=_float_or_none(row.get("volatility_20")),
        max_drawdown=_float_or_none(row.get("max_drawdown")),
        volume_ratio_5_20=_float_or_none(row.get("volume_ratio_5_20")),
        score=int(row["score"]) if row.get("score") is not None else None,
        signal=str(row.get("signal") or "数据不足"),
        model_status="Qlib 股票池评分缓存",
        error=str(row.get("error") or ""),
    )
    return QlibScreenItem(target=target, analysis=analysis)


def serialize_qlib_screen_result(result: QlibScreenResult) -> dict[str, Any]:
    return {
        "pool": result.pool,
        "source": result.source,
        "target_count": result.target_count,
        "analyzed_count": result.analyzed_count,
        "failed_count": result.failed_count,
        "error": result.error,
        "scoring_rules": list(QLIB_FACTOR_SCORE_RULES),
        "items": [
            {
                "pool": item.target.pool,
                "market": item.target.market,
                "symbol": item.target.symbol,
                "name": item.target.name,
                "qlib_symbol": item.analysis.qlib_symbol,
                "score": item.analysis.score,
                "signal": item.analysis.signal,
                "row_count": item.analysis.row_count,
                "source": item.analysis.source,
                "start_date": item.analysis.start_date,
                "end_date": item.analysis.end_date,
                "latest_close": item.analysis.latest_close,
                "latest_change_percent": item.analysis.latest_change_percent,
                "return_5": item.analysis.return_5,
                "return_20": item.analysis.return_20,
                "return_60": item.analysis.return_60,
                "ma_20_distance": item.analysis.ma_20_distance,
                "volatility_20": item.analysis.volatility_20,
                "max_drawdown": item.analysis.max_drawdown,
                "volume_ratio_5_20": item.analysis.volume_ratio_5_20,
                "error": item.analysis.error,
            }
            for item in result.items
        ],
    }


def serialize_qlib_strategy_search_result(result: QlibStrategySearchResult) -> dict[str, Any]:
    return {
        "pool": result.pool,
        "source": result.source,
        "years": list(result.years),
        "limit": result.limit,
        "benchmark_name": result.benchmark_name,
        "min_annual_return_percent": result.min_annual_return_percent,
        "require_beat_benchmark": result.require_beat_benchmark,
        "qualified_count": result.qualified_count,
        "done_count": result.done_count,
        "candidate_count": result.candidate_count,
        "status": result.status,
        "error": result.error,
        "items": [
            {
                "key": item.candidate.key,
                "name": item.candidate.name,
                "score_threshold": item.candidate.score_threshold,
                "score_profile": item.candidate.score_profile,
                "take_profit_percent": item.candidate.take_profit_percent,
                "stop_loss_percent": item.candidate.stop_loss_percent,
                "max_holding_days": item.candidate.max_holding_days,
                "cost_rate": item.candidate.cost_rate,
                "total_profit": item.total_profit,
                "average_return_percent": item.average_return_percent,
                "min_return_percent": item.min_return_percent,
                "average_excess_return_percent": item.average_excess_return_percent,
                "min_excess_return_percent": item.min_excess_return_percent,
                "profitable_year_count": item.profitable_year_count,
                "beat_benchmark_year_count": item.beat_benchmark_year_count,
                "tested_year_count": item.tested_year_count,
                "all_years_profitable": item.all_years_profitable,
                "all_years_beat_benchmark": item.all_years_beat_benchmark,
                "is_qualified": item.is_qualified,
                "qualification_note": item.qualification_note,
                "years": [
                    {
                        "year": year.year,
                        "start_date": year.start_date,
                        "end_date": year.end_date,
                        "total_profit": year.total_profit,
                        "return_percent": year.return_percent,
                        "max_capital_used": year.max_capital_used,
                        "total_fee": year.total_fee,
                        "trade_count": year.trade_count,
                        "tested_count": year.tested_count,
                        "skipped_count": year.skipped_count,
                        "benchmark_name": year.benchmark_name,
                        "benchmark_return_percent": year.benchmark_return_percent,
                        "excess_return_percent": year.excess_return_percent,
                    }
                    for year in item.years
                ],
            }
            for item in result.items
        ],
    }


def serialize_qlib_rotation_strategy_search_result(result: QlibRotationStrategySearchResult) -> dict[str, Any]:
    return {
        "pool": result.pool,
        "source": result.source,
        "years": list(result.years),
        "limit": result.limit,
        "benchmark_name": result.benchmark_name,
        "min_annual_return_percent": result.min_annual_return_percent,
        "require_beat_benchmark": result.require_beat_benchmark,
        "qualified_count": result.qualified_count,
        "done_count": result.done_count,
        "candidate_count": result.candidate_count,
        "status": result.status,
        "error": result.error,
        "items": [
            {
                "key": item.candidate.key,
                "name": item.candidate.name,
                "score_profile": item.candidate.score_profile,
                "rank_metric": item.candidate.rank_metric,
                "market_filter": item.candidate.market_filter,
                "score_threshold": item.candidate.score_threshold,
                "min_amount": item.candidate.min_amount,
                "top_n": item.candidate.top_n,
                "rebalance": item.candidate.rebalance,
                "cost_rate": item.candidate.cost_rate,
                "total_profit": item.total_profit,
                "average_return_percent": item.average_return_percent,
                "min_return_percent": item.min_return_percent,
                "average_excess_return_percent": item.average_excess_return_percent,
                "min_excess_return_percent": item.min_excess_return_percent,
                "profitable_year_count": item.profitable_year_count,
                "beat_benchmark_year_count": item.beat_benchmark_year_count,
                "tested_year_count": item.tested_year_count,
                "all_years_profitable": item.all_years_profitable,
                "all_years_beat_benchmark": item.all_years_beat_benchmark,
                "is_qualified": item.is_qualified,
                "qualification_note": item.qualification_note,
                "years": [
                    {
                        "year": year.year,
                        "start_date": year.start_date,
                        "end_date": year.end_date,
                        "total_profit": year.total_profit,
                        "return_percent": year.return_percent,
                        "max_capital_used": year.max_capital_used,
                        "total_fee": year.total_fee,
                        "trade_count": year.trade_count,
                        "tested_count": year.tested_count,
                        "skipped_count": year.skipped_count,
                        "benchmark_name": year.benchmark_name,
                        "benchmark_return_percent": year.benchmark_return_percent,
                        "excess_return_percent": year.excess_return_percent,
                    }
                    for year in item.years
                ],
            }
            for item in result.items
        ],
    }


def serialize_hk_connect_momentum_review_result(result: HkConnectMomentumReviewResult) -> dict[str, Any]:
    def serialize_candidate(item: HkConnectMomentumReviewCandidate) -> dict[str, Any]:
        return {
            "rank": item.rank,
            "market": item.market,
            "symbol": item.symbol,
            "name": item.name,
            "signal_score": item.signal_score,
            "return_10_percent": item.return_10_percent,
            "amount": item.amount,
            "average_amount_20": item.average_amount_20,
            "close": item.close,
            "lot_size": item.lot_size,
            "lot_value": item.lot_value,
            "budget_lots": item.budget_lots,
            "estimated_cash": item.estimated_cash,
            "market_cap": item.market_cap,
            "selected": item.selected,
        }

    return {
        "strategy_key": result.strategy_key,
        "strategy_name": result.strategy_name,
        "source": result.source,
        "status": result.status,
        "generated_at": result.generated_at,
        "signal_date": result.signal_date,
        "hsi_date": result.hsi_date,
        "hsi_close": result.hsi_close,
        "hsi_ma60": result.hsi_ma60,
        "hsi_filter_passed": result.hsi_filter_passed,
        "action": result.action,
        "summary": result.summary,
        "pool_count": result.pool_count,
        "usable_count": result.usable_count,
        "capital": result.capital,
        "max_position_percent": result.max_position_percent,
        "single_position_budget": result.single_position_budget,
        "cost_rate": result.cost_rate,
        "universe_limit": result.universe_limit,
        "min_market_cap": result.min_market_cap,
        "min_amount": result.min_amount,
        "top_n": result.top_n,
        "lookback_days": result.lookback_days,
        "volume_window_days": result.volume_window_days,
        "hold_days": result.hold_days,
        "error": result.error,
        "candidates": [serialize_candidate(item) for item in result.candidates],
        "selected": [serialize_candidate(item) for item in result.selected],
    }


def serialize_ranked_rotation_backtest_result(result: QlibRankedRotationBacktestResult) -> dict[str, Any]:
    hsi = next((item for item in result.benchmarks if item.name == "恒生指数"), None)
    hsi_return = hsi.return_percent if hsi is not None else None
    return {
        "pool": result.pool,
        "source": result.source,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "score_profile": result.score_profile,
        "rank_metric": result.rank_metric,
        "market_filter": result.market_filter,
        "score_threshold": result.score_threshold,
        "top_n": result.top_n,
        "rebalance": result.rebalance,
        "min_amount": result.min_amount,
        "cost_rate": result.cost_rate,
        "target_count": result.target_count,
        "tested_count": result.tested_count,
        "skipped_count": result.skipped_count,
        "total_profit": result.total_profit,
        "return_percent": result.return_percent,
        "max_capital_used": result.max_capital_used,
        "total_fee": result.total_fee,
        "trade_count": result.trade_count,
        "closed_trade_count": result.closed_trade_count,
        "benchmark_name": hsi.name if hsi is not None else "恒生指数",
        "benchmark_return_percent": hsi_return,
        "excess_return_percent": (
            result.return_percent - hsi_return
            if result.return_percent is not None and hsi_return is not None
            else None
        ),
        "benchmarks": [serialize_index_benchmark(item) for item in result.benchmarks],
        "error": result.error,
    }


def diagnose_hk_pool_ranked_rotation_selection(
    *,
    start_date: str,
    end_date: str,
    limit: int | None = None,
    score_profile: str = "balanced",
    rank_metric: str = "score",
    market_filter: str = "none",
    score_threshold: int = 0,
    min_amount: float = 0,
    top_n: int = 5,
    rebalance: str = "quarterly",
    refresh: bool = False,
) -> tuple[QlibRankedRotationDiagnosisPeriod, ...]:
    normalized_profile = normalize_score_profile(score_profile)
    rank_metric_text = str(rank_metric).lower()
    normalized_rank_metric = (
        rank_metric_text
        if rank_metric_text
        in {
            "relative_momentum_60",
            "relative_reversal_60",
            "volume_breakout_rank",
            "value_quality_rank",
            "value_score_rank",
            "adaptive_value_breakout",
        }
        else "score"
    )
    normalized_rebalance = str(rebalance).lower()
    if normalized_rebalance not in {"weekly", "monthly", "quarterly"}:
        normalized_rebalance = "quarterly"
    market_filter_text = str(market_filter).lower()
    normalized_market_filter = market_filter_text if market_filter_text in {"hsi_ma60", "hsi_ma120", "hsi_ma200"} else "none"

    targets = list_hk_screen_targets(limit=limit)
    with connect_market_data_db() as conn:
        rows_by_target = _read_cached_daily_rows_for_targets_with_conn(conn, targets)
    row_maps: dict[tuple[str, str], dict[str, AkshareStockHistoryRow]] = {}
    previous_score_by_date: dict[tuple[str, str], dict[str, int | None]] = {}
    previous_relative_momentum_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    previous_volume_breakout_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    previous_value_quality_by_date: dict[tuple[str, str], dict[str, float | None]] = {}
    all_dates: set[str] = set()
    needs_index = normalized_rank_metric in {"relative_momentum_60", "relative_reversal_60", "adaptive_value_breakout"} or normalized_market_filter != "none"
    index_rows = load_index_rows(market="HK", symbol="HSI", refresh=refresh) if needs_index else ()
    index_return_by_date = _index_period_return_by_date(rows=index_rows, periods=60) if normalized_rank_metric in {"relative_momentum_60", "relative_reversal_60", "adaptive_value_breakout"} else {}
    market_allowed_by_date = _market_allowed_by_date(rows=index_rows, filter_name=normalized_market_filter)
    for target in targets:
        key = (target.market, target.symbol)
        rows = tuple(row for row in rows_by_target.get(key, ()) if row.date <= end_date)
        if not rows:
            continue
        row_maps[key] = {row.date: row for row in rows}
        score_by_date = _daily_factor_scores(rows, score_profile=normalized_profile)
        previous_score_by_date[key] = _previous_value_by_date(rows, score_by_date)
        if normalized_rank_metric in {"relative_momentum_60", "relative_reversal_60"}:
            relative_by_date = _relative_momentum_by_date(rows, index_return_by_date=index_return_by_date, periods=60)
            previous_relative_momentum_by_date[key] = _previous_value_by_date(rows, relative_by_date)
        if normalized_rank_metric in {"volume_breakout_rank", "adaptive_value_breakout"}:
            breakout_by_date = _volume_breakout_rank_by_date(rows)
            previous_volume_breakout_by_date[key] = _previous_value_by_date(rows, breakout_by_date)
        if normalized_rank_metric in {"value_quality_rank", "value_score_rank", "adaptive_value_breakout"}:
            value_quality_by_date = _value_quality_rank_by_date(rows, symbol=target.symbol, refresh=refresh)
            previous_value_quality_by_date[key] = _previous_value_by_date(rows, value_quality_by_date)
        all_dates.update(row.date for row in rows if start_date <= row.date <= end_date)

    ordered_dates = sorted(all_dates)
    rebalance_dates: list[str] = []
    last_bucket = ""
    for date in ordered_dates:
        bucket = _rebalance_bucket(date, normalized_rebalance)
        if bucket != last_bucket:
            last_bucket = bucket
            if market_allowed_by_date.get(date, True):
                rebalance_dates.append(date)
    periods: list[QlibRankedRotationDiagnosisPeriod] = []
    top_n_value = max(1, int(top_n))
    for index, current_date in enumerate(rebalance_dates):
        next_date = rebalance_dates[index + 1] if index + 1 < len(rebalance_dates) else end_date
        ranked: list[tuple[float, tuple[str, str], AkshareStockHistoryRow]] = []
        realized: list[tuple[float, tuple[str, str]]] = []
        for key, row_map in row_maps.items():
            row = row_map.get(current_date)
            if row is None:
                continue
            score = previous_score_by_date.get(key, {}).get(current_date)
            if score is None or score < score_threshold:
                continue
            amount = _finite_float(row.amount)
            if amount is not None and amount < min_amount:
                continue
            rank_value: float | None
            if normalized_rank_metric in {"relative_momentum_60", "relative_reversal_60"}:
                rank_value = previous_relative_momentum_by_date.get(key, {}).get(current_date)
                if normalized_rank_metric == "relative_reversal_60" and rank_value is not None:
                    rank_value = -rank_value
            elif normalized_rank_metric == "volume_breakout_rank":
                rank_value = previous_volume_breakout_by_date.get(key, {}).get(current_date)
            elif normalized_rank_metric == "value_quality_rank":
                rank_value = previous_value_quality_by_date.get(key, {}).get(current_date)
            elif normalized_rank_metric == "value_score_rank":
                value_quality = previous_value_quality_by_date.get(key, {}).get(current_date)
                rank_value = _value_score_rank_value(score=score, value_quality=value_quality)
            elif normalized_rank_metric == "adaptive_value_breakout":
                index_return = index_return_by_date.get(current_date)
                if index_return is not None and index_return > 0:
                    rank_value = previous_volume_breakout_by_date.get(key, {}).get(current_date)
                else:
                    rank_value = previous_value_quality_by_date.get(key, {}).get(current_date)
            else:
                rank_value = float(score)
            if rank_value is None:
                continue
            ranked.append((float(rank_value), key, row))
            realized_return = _realized_return_between(row_map, current_date, next_date)
            if realized_return is not None:
                realized.append((realized_return, key))
        ranked.sort(key=lambda item: item[0], reverse=True)
        realized.sort(key=lambda item: item[0], reverse=True)
        selected_keys = {key for _rank, key, _row in ranked[:top_n_value]}
        selected_returns = [
            _realized_return_between(row_maps[key], current_date, next_date)
            for _rank, key, _row in ranked[:top_n_value]
        ]
        selected_returns = [value for value in selected_returns if value is not None]
        best_returns = [value for value, _key in realized[:top_n_value]]
        periods.append(QlibRankedRotationDiagnosisPeriod(
            start_date=current_date,
            end_date=next_date,
            selected=tuple(
                (
                    key[1],
                    _target_name_for_key(targets, key),
                    _realized_return_between(row_maps[key], current_date, next_date),
                )
                for _rank, key, _row in ranked[:top_n_value]
            ),
            realized_best=tuple(
                (key[1], _target_name_for_key(targets, key), value)
                for value, key in realized[:top_n_value]
            ),
            selected_average_return=sum(selected_returns) / len(selected_returns) if selected_returns else None,
            best_average_return=sum(best_returns) / len(best_returns) if best_returns else None,
            hit_count=sum(1 for _value, key in realized[:top_n_value] if key in selected_keys),
        ))
    return tuple(periods)


def _realized_return_between(
    row_map: dict[str, AkshareStockHistoryRow],
    start_date: str,
    end_date: str,
) -> float | None:
    start_row = row_map.get(start_date)
    if start_row is None:
        return None
    start_price = _finite_float(start_row.open) or _finite_float(start_row.close)
    end_candidates = [date for date in row_map if start_date < date <= end_date]
    if not end_candidates or start_price is None:
        return None
    end_row = row_map[max(end_candidates)]
    end_price = _finite_float(end_row.close)
    return (end_price / start_price - 1) * 100 if end_price is not None and start_price else None


def _target_name_for_key(targets: tuple[QlibScreenTarget, ...], key: tuple[str, str]) -> str:
    for target in targets:
        if target.market == key[0] and target.symbol == key[1]:
            return target.name
    return key[1]


def _strategy_search_cache_key(
    *,
    years: tuple[int, ...],
    limit: int | None,
    candidates: tuple[QlibStrategyCandidate, ...],
    force_liquidate_end: bool,
    min_annual_return_percent: float,
    require_beat_benchmark: bool,
) -> dict[str, Any]:
    return {
        "years": list(years),
        "limit": limit,
        "force_liquidate_end": bool(force_liquidate_end),
        "min_annual_return_percent": float(min_annual_return_percent),
        "require_beat_benchmark": bool(require_beat_benchmark),
        "candidates": [
            {
                "key": candidate.key,
                "score_threshold": int(candidate.score_threshold),
                "score_profile": candidate.score_profile,
                "take_profit_percent": float(candidate.take_profit_percent),
                "stop_loss_percent": float(candidate.stop_loss_percent),
                "max_holding_days": int(candidate.max_holding_days),
                "cost_rate": float(candidate.cost_rate),
            }
            for candidate in candidates
        ],
    }


def _read_strategy_search_cache(path, cache_key: dict[str, Any]) -> QlibStrategySearchResult | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
        return None
    rows = payload.get("items")
    if not isinstance(rows, list):
        return None
    items: list[QlibStrategySearchItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = QlibStrategyCandidate(
            key=str(row.get("key") or ""),
            name=str(row.get("name") or ""),
            score_threshold=int(row.get("score_threshold") or 0),
            score_profile=normalize_score_profile(row.get("score_profile")),
            take_profit_percent=float(row.get("take_profit_percent") or 0),
            stop_loss_percent=float(row.get("stop_loss_percent") or 0),
            max_holding_days=int(row.get("max_holding_days") or 0),
            cost_rate=float(row.get("cost_rate") or 0),
        )
        years: list[QlibStrategyYearResult] = []
        for year_row in row.get("years") or []:
            if not isinstance(year_row, dict):
                continue
            years.append(QlibStrategyYearResult(
                year=int(year_row.get("year") or 0),
                start_date=str(year_row.get("start_date") or ""),
                end_date=str(year_row.get("end_date") or ""),
                total_profit=float(year_row.get("total_profit") or 0),
                return_percent=_float_or_none(year_row.get("return_percent")),
                max_capital_used=float(year_row.get("max_capital_used") or 0),
                total_fee=float(year_row.get("total_fee") or 0),
                trade_count=int(year_row.get("trade_count") or 0),
                tested_count=int(year_row.get("tested_count") or 0),
                skipped_count=int(year_row.get("skipped_count") or 0),
                benchmark_name=str(year_row.get("benchmark_name") or ""),
                benchmark_return_percent=_float_or_none(year_row.get("benchmark_return_percent")),
                excess_return_percent=_float_or_none(year_row.get("excess_return_percent")),
            ))
        items.append(QlibStrategySearchItem(
            candidate=candidate,
            years=tuple(years),
            total_profit=float(row.get("total_profit") or 0),
            average_return_percent=_float_or_none(row.get("average_return_percent")),
            min_return_percent=_float_or_none(row.get("min_return_percent")),
            average_excess_return_percent=_float_or_none(row.get("average_excess_return_percent")),
            min_excess_return_percent=_float_or_none(row.get("min_excess_return_percent")),
            profitable_year_count=int(row.get("profitable_year_count") or 0),
            beat_benchmark_year_count=int(row.get("beat_benchmark_year_count") or 0),
            tested_year_count=int(row.get("tested_year_count") or len(years)),
            all_years_profitable=bool(row.get("all_years_profitable")),
            all_years_beat_benchmark=bool(row.get("all_years_beat_benchmark")),
            is_qualified=bool(row.get("is_qualified")),
            qualification_note=str(row.get("qualification_note") or ("达标" if row.get("is_qualified") else "未达标")),
        ))
    return QlibStrategySearchResult(
        pool=HK_POOL,
        source=str(payload.get("source") or "cache:hk_pool_strategy_search"),
        years=tuple(int(year) for year in payload.get("years") or []),
        limit=_int_or_none(payload.get("limit")),
        benchmark_name=str(payload.get("benchmark_name") or "恒生指数"),
        items=tuple(items),
        min_annual_return_percent=float(payload.get("min_annual_return_percent") or cache_key.get("min_annual_return_percent") or 5),
        require_beat_benchmark=bool(payload.get("require_beat_benchmark", cache_key.get("require_beat_benchmark", True))),
        qualified_count=int(payload.get("qualified_count") or sum(1 for item in items if item.is_qualified)),
        done_count=int(payload.get("done_count") or len(items)),
        candidate_count=int(payload.get("candidate_count") or len(items)),
        status=str(payload.get("status") or "done"),
        error=str(payload.get("error") or ""),
    )


def _write_strategy_search_cache(path, cache_key: dict[str, Any], result: QlibStrategySearchResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = serialize_qlib_strategy_search_result(result)
    payload["cache_key"] = cache_key
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def serialize_qlib_pool_backtest_result(result: QlibPoolBacktestResult, *, detail_limit: int | None = None) -> dict[str, Any]:
    items = result.items[: max(0, int(detail_limit))] if detail_limit is not None else result.items
    return {
        "pool": result.pool,
        "source": result.source,
        "target_count": result.target_count,
        "tested_count": result.tested_count,
        "skipped_count": result.skipped_count,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "score_threshold": result.score_threshold,
        "score_profile": result.score_profile,
        "take_profit_percent": result.take_profit_percent,
        "stop_loss_percent": result.stop_loss_percent,
        "max_holding_days": result.max_holding_days,
        "cost_rate": result.cost_rate,
        "total_profit": result.total_profit,
        "total_invested": result.total_invested,
        "total_fee": result.total_fee,
        "max_capital_used": result.max_capital_used,
        "trade_count": result.trade_count,
        "closed_trade_count": result.closed_trade_count,
        "open_position_count": result.open_position_count,
        "force_liquidate_end": result.force_liquidate_end,
        "benchmarks": [
            {
                **serialize_index_benchmark(item),
                "excess_return_percent": (
                    result.total_profit / result.max_capital_used * 100 - item.return_percent
                    if result.max_capital_used and item.return_percent is not None
                    else None
                ),
            }
            for item in result.benchmarks
        ],
        "error": result.error,
        "items": [
            _serialize_pool_backtest_item(item)
            for item in items
        ],
    }


def _cache_key_digest(cache_key: dict[str, Any]) -> str:
    raw = json.dumps(cache_key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _hk_pool_backtest_cache_path(cache_key: dict[str, Any]):
    base_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_backtests"
    return base_path / f"{_cache_key_digest(cache_key)}.json"


def _serialize_pool_backtest_item(item: QlibPoolBacktestItem) -> dict[str, Any]:
    result = item.result
    return {
        "market": item.target.market,
        "symbol": item.target.symbol,
        "name": item.target.name,
        "lot_size": item.lot_size,
        "total_profit": result.total_profit if result is not None else 0,
        "total_return_percent": result.total_return_percent if result is not None else 0,
        "total_invested": result.total_invested if result is not None else 0,
        "max_capital_used": result.max_capital_used if result is not None else 0,
        "trade_count": result.trade_count if result is not None else 0,
        "closed_trade_count": result.closed_trade_count if result is not None else 0,
        "open_position_shares": result.open_position_shares if result is not None else 0,
        "start_date": result.start_date if result is not None else "",
        "end_date": result.end_date if result is not None else "",
        "error": item.error,
    }


def _read_hk_pool_backtest_cache(path, cache_key: dict[str, Any]) -> QlibPoolBacktestResult | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
        return None
    rows = payload.get("items")
    if not isinstance(rows, list):
        return None
    items: list[QlibPoolBacktestItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        target = QlibScreenTarget(
            market=str(row.get("market") or "HK"),
            symbol=_normalize_hk_symbol(row.get("symbol")),
            name=str(row.get("name") or ""),
            pool=HK_POOL,
        )
        serialized_result = row.get("result")
        result = _backtest_result_from_serialized(serialized_result) if isinstance(serialized_result, dict) else None
        items.append(QlibPoolBacktestItem(target=target, lot_size=_int_or_none(row.get("lot_size")), result=result, error=str(row.get("error") or "")))
    return QlibPoolBacktestResult(
        pool=HK_POOL,
        source=str(payload.get("source") or "cache:hk_pool_backtest"),
        target_count=int(payload.get("target_count") or len(items)),
        tested_count=int(payload.get("tested_count") or 0),
        skipped_count=int(payload.get("skipped_count") or 0),
        start_date=str(payload.get("start_date") or cache_key["start_date"]),
        end_date=str(payload.get("end_date") or cache_key["end_date"]),
        score_threshold=int(payload.get("score_threshold") or cache_key["score_threshold"]),
        score_profile=normalize_score_profile(payload.get("score_profile") or cache_key.get("score_profile")),
        take_profit_percent=float(payload.get("take_profit_percent") or cache_key["take_profit_percent"]),
        stop_loss_percent=float(payload.get("stop_loss_percent") or cache_key.get("stop_loss_percent") or 0),
        max_holding_days=int(payload.get("max_holding_days") or cache_key.get("max_holding_days") or 0),
        cost_rate=float(payload.get("cost_rate") or cache_key["cost_rate"]),
        total_profit=float(payload.get("total_profit") or 0),
        total_invested=float(payload.get("total_invested") or 0),
        total_fee=float(payload.get("total_fee") or 0),
        max_capital_used=float(payload.get("max_capital_used") or 0),
        trade_count=int(payload.get("trade_count") or 0),
        closed_trade_count=int(payload.get("closed_trade_count") or 0),
        open_position_count=int(payload.get("open_position_count") or 0),
        benchmarks=tuple(_benchmark_from_serialized(row) for row in payload.get("benchmarks") or [] if isinstance(row, dict)),
        items=tuple(items),
        force_liquidate_end=bool(payload.get("force_liquidate_end", cache_key.get("force_liquidate_end", True))),
        error=str(payload.get("error") or ""),
    )


def _write_hk_pool_backtest_cache(path, cache_key: dict[str, Any], result: QlibPoolBacktestResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache_key": cache_key,
        "source": result.source,
        "target_count": result.target_count,
        "tested_count": result.tested_count,
        "skipped_count": result.skipped_count,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "score_threshold": result.score_threshold,
        "score_profile": result.score_profile,
        "take_profit_percent": result.take_profit_percent,
        "stop_loss_percent": result.stop_loss_percent,
        "max_holding_days": result.max_holding_days,
        "cost_rate": result.cost_rate,
        "total_profit": result.total_profit,
        "total_invested": result.total_invested,
        "total_fee": result.total_fee,
        "max_capital_used": result.max_capital_used,
        "trade_count": result.trade_count,
        "closed_trade_count": result.closed_trade_count,
        "open_position_count": result.open_position_count,
        "force_liquidate_end": result.force_liquidate_end,
        "benchmarks": [serialize_index_benchmark(item) for item in result.benchmarks],
        "error": result.error,
        "items": [
            {
                "market": item.target.market,
                "symbol": item.target.symbol,
                "name": item.target.name,
                "lot_size": item.lot_size,
                "error": item.error,
                "result": serialize_qlib_backtest_result(item.result) if item.result is not None else None,
            }
            for item in result.items
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _backtest_result_from_serialized(row: dict[str, Any]) -> QlibBacktestResult:
    from .qlib_bridge import QlibBacktestPoint, QlibBacktestTrade

    return QlibBacktestResult(
        market=str(row.get("market") or "HK"),
        symbol=str(row.get("symbol") or ""),
        name=str(row.get("name") or ""),
        start_date=str(row.get("start_date") or ""),
        end_date=str(row.get("end_date") or ""),
        lot_size=int(row.get("lot_size") or 0),
        score_threshold=int(row.get("score_threshold") or 0),
        score_profile=normalize_score_profile(row.get("score_profile")),
        take_profit_percent=float(row.get("take_profit_percent") or 0),
        stop_loss_percent=float(row.get("stop_loss_percent") or 0),
        max_holding_days=int(row.get("max_holding_days") or 0),
        cost_rate=float(row.get("cost_rate") or 0),
        capital_mode=str(row.get("capital_mode") or "unlimited"),
        initial_capital=float(row.get("initial_capital") or 0),
        total_invested=float(row.get("total_invested") or 0),
        total_fee=float(row.get("total_fee") or 0),
        max_capital_used=float(row.get("max_capital_used") or 0),
        final_equity=float(row.get("final_equity") or 0),
        total_profit=float(row.get("total_profit") or 0),
        total_return_percent=float(row.get("total_return_percent") or 0),
        trade_count=int(row.get("trade_count") or 0),
        closed_trade_count=int(row.get("closed_trade_count") or 0),
        open_position_shares=int(row.get("open_position_shares") or 0),
        points=tuple(
            QlibBacktestPoint(
                date=str(point.get("date") or ""),
                close=float(point.get("close") or 0),
                score=int(point["score"]) if point.get("score") is not None else None,
                cash=float(point.get("cash") or 0),
                position_value=float(point.get("position_value") or 0),
                equity=float(point.get("equity") or 0),
                action=str(point.get("action") or ""),
            )
            for point in row.get("points") or []
            if isinstance(point, dict)
        ),
        trades=tuple(
            QlibBacktestTrade(
                trigger_date=str(trade.get("trigger_date") or ""),
                trigger_score=int(trade.get("trigger_score") or 0),
                buy_date=str(trade.get("buy_date") or ""),
                buy_price=float(trade.get("buy_price") or 0),
                sell_date=str(trade.get("sell_date") or ""),
                sell_price=_float_or_none(trade.get("sell_price")),
                lot_size=int(trade.get("lot_size") or 0),
                shares=int(trade.get("shares") or 0),
                buy_cost=float(trade.get("buy_cost") or 0),
                sell_proceeds=_float_or_none(trade.get("sell_proceeds")),
                realized_profit=_float_or_none(trade.get("realized_profit")),
                realized_return_percent=_float_or_none(trade.get("realized_return_percent")),
                holding_days=int(trade.get("holding_days") or 0),
                status=str(trade.get("status") or ""),
            )
            for trade in row.get("trades") or []
            if isinstance(trade, dict)
        ),
        rules=tuple(str(rule) for rule in row.get("rules") or []),
        source=str(row.get("source") or ""),
        force_liquidate_end=bool(row.get("force_liquidate_end", True)),
        error=str(row.get("error") or ""),
    )


def _benchmark_from_serialized(row: dict[str, Any]) -> IndexBenchmark:
    return IndexBenchmark(
        market=str(row.get("market") or ""),
        symbol=str(row.get("symbol") or ""),
        name=str(row.get("name") or ""),
        start_date=str(row.get("start_date") or ""),
        end_date=str(row.get("end_date") or ""),
        start_close=_float_or_none(row.get("start_close")),
        end_close=_float_or_none(row.get("end_close")),
        return_percent=_float_or_none(row.get("return_percent")),
        source=str(row.get("source") or ""),
        error=str(row.get("error") or ""),
    )


def _int_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _screen_one_target(target: QlibScreenTarget, *, refresh: bool) -> QlibFactorAnalysis:
    qlib_target = target.qlib_target
    rows: tuple[AkshareStockHistoryRow, ...] = ()
    source = "cache"
    error = ""

    if refresh:
        try:
            history = fetch_akshare_stock_history(
                market=target.market,
                symbol=target.symbol,
                name=target.name,
                period="daily",
                start_date=target.start_date,
                end_date=dt.date.today().isoformat(),
                adjust="",
            )
            rows = history.rows
            source = "akshare"
            if rows:
                _cache_daily_rows(qlib_target, rows)
        except Exception as exc:
            error = f"AKShare 刷新失败，尝试使用本地缓存：{exc}"

    if not rows:
        rows = _read_cached_daily_rows(qlib_target)
        source = "cache"

    item = type("_ScreenItem", (), {
        "source": source,
        "error": error or ("" if rows else "没有可分析的本地日线数据"),
    })()
    return _analyze_rows(target=qlib_target, item=item, rows=rows)


def _load_hk_pool_rows(*, refresh: bool = False) -> tuple[tuple[dict[str, Any], ...], str]:
    cache_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_targets.json"
    if not refresh:
        cached = _read_hk_pool_cache(cache_path)
        if cached:
            return cached, "cache:hk_pool_targets"
    try:
        import akshare as ak
        frame = ak.stock_hk_spot()
        rows = tuple(frame.to_dict("records"))
        _write_hk_pool_cache(cache_path, rows)
        return rows, "akshare:stock_hk_spot:sina_all_hk"
    except Exception as exc:
        cached = _read_hk_pool_cache(cache_path)
        if cached:
            return cached, f"cache:hk_pool_targets; akshare-error:{exc}"
        return (), f"akshare-error:{exc}"


def _read_hk_pool_cache(path) -> tuple[dict[str, Any], ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(data, list):
        return ()
    return tuple(row for row in data if isinstance(row, dict))


def _write_hk_pool_cache(path, rows: tuple[dict[str, Any], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(rows), ensure_ascii=False), encoding="utf-8")


def _read_cached_daily_rows(target: QlibWatchTarget) -> tuple[AkshareStockHistoryRow, ...]:
    query = """
        SELECT time_key, open, close, high, low, volume, turnover, change_rate, turnover_rate
        FROM market_kline
        WHERE provider = ?
          AND market = ?
          AND symbol = ?
          AND ktype = 'daily'
          AND autype = 'none'
          AND time_key >= ?
        ORDER BY time_key
    """
    with connect_market_data_db() as conn:
        rows = conn.execute(
            query,
            (MARKET_DATA_PROVIDER_AKSHARE, target.market, target.symbol, target.start_date),
        ).fetchall()
    return tuple(
        AkshareStockHistoryRow(
            date=str(row["time_key"] or "")[:10],
            symbol=target.symbol,
            open=row["open"],
            close=row["close"],
            high=row["high"],
            low=row["low"],
            volume=row["volume"],
            amount=row["turnover"],
            amplitude=None,
            change_percent=row["change_rate"],
            change_amount=None,
            turnover_rate=row["turnover_rate"],
        )
        for row in rows
    )


def _read_cached_daily_rows_for_targets_with_conn(
    conn,
    targets: tuple[QlibScreenTarget, ...],
) -> dict[tuple[str, str], tuple[AkshareStockHistoryRow, ...]]:
    if not targets:
        return {}
    rows_by_target: dict[tuple[str, str], list[AkshareStockHistoryRow]] = {
        (target.market, target.symbol): []
        for target in targets
    }
    targets_by_market: dict[str, list[QlibScreenTarget]] = {}
    for target in targets:
        targets_by_market.setdefault(target.market, []).append(target)

    for market, market_targets in targets_by_market.items():
        symbols = [target.symbol for target in market_targets]
        min_start_date = min(target.start_date for target in market_targets)
        placeholders = ",".join("?" for _ in symbols)
        query = f"""
            SELECT market, symbol, time_key, open, close, high, low, volume, turnover, change_rate, turnover_rate
            FROM market_kline
            WHERE provider = ?
              AND market = ?
              AND symbol IN ({placeholders})
              AND ktype = 'daily'
              AND autype = 'none'
              AND time_key >= ?
            ORDER BY symbol, time_key
        """
        rows = conn.execute(
            query,
            (MARKET_DATA_PROVIDER_AKSHARE, market, *symbols, min_start_date),
        ).fetchall()
        target_start_dates = {
            (target.market, target.symbol): target.start_date
            for target in market_targets
        }
        for row in rows:
            key = (str(row["market"]), str(row["symbol"]))
            if str(row["time_key"] or "") < target_start_dates.get(key, QLIB_EXPORT_START_DATE):
                continue
            rows_by_target.setdefault(key, []).append(
                AkshareStockHistoryRow(
                    date=str(row["time_key"] or "")[:10],
                    symbol=str(row["symbol"]),
                    open=row["open"],
                    close=row["close"],
                    high=row["high"],
                    low=row["low"],
                    volume=row["volume"],
                    amount=row["turnover"],
                    amplitude=None,
                    change_percent=row["change_rate"],
                    change_amount=None,
                    turnover_rate=row["turnover_rate"],
                )
            )
    return {
        key: tuple(rows)
        for key, rows in rows_by_target.items()
    }


def _read_cached_daily_rows_with_conn(conn, target: QlibWatchTarget) -> tuple[AkshareStockHistoryRow, ...]:
    query = """
        SELECT time_key, open, close, high, low, volume, turnover, change_rate, turnover_rate
        FROM market_kline
        WHERE provider = ?
          AND market = ?
          AND symbol = ?
          AND ktype = 'daily'
          AND autype = 'none'
          AND time_key >= ?
        ORDER BY time_key
    """
    rows = conn.execute(
        query,
        (MARKET_DATA_PROVIDER_AKSHARE, target.market, target.symbol, target.start_date),
    ).fetchall()
    return tuple(
        AkshareStockHistoryRow(
            date=str(row["time_key"] or "")[:10],
            symbol=target.symbol,
            open=row["open"],
            close=row["close"],
            high=row["high"],
            low=row["low"],
            volume=row["volume"],
            amount=row["turnover"],
            amplitude=None,
            change_percent=row["change_rate"],
            change_amount=None,
            turnover_rate=row["turnover_rate"],
        )
        for row in rows
    )


def _normalize_hk_symbol(value: Any) -> str:
    text = "".join(ch for ch in str(value or "").strip().upper() if ch.isalnum())
    return text.zfill(5) if text else ""


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
