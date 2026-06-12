from __future__ import annotations

import csv
import datetime as dt
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.settings import get_settings

from .akshare_market import AkshareStockHistoryRow, fetch_akshare_stock_history, _normalize_ohlc_prices
from .market_data import (
    MARKET_DATA_PROVIDER_AKSHARE,
    MarketHistoryTarget,
    connect_market_data_db,
    upsert_kline_rows,
)


QLIB_REPO_PATH = Path(r"D:\home\chenkunze\slns+\qlib")
QLIB_EXPORT_START_DATE = "1990-01-01"
QLIB_FACTOR_SCORE_RULES = (
    "基础分 = 50",
    "5日动量 > 0：+12，否则 -8",
    "20日动量 > 0：+12，否则 -10",
    "20日均线偏离 -8% ~ 3%：+10；> 10%：-10；< -15%：-6",
    "最大回撤 < -25%：-8；-18% ~ -5%：+6",
    "20日波动 > 70%：-8",
    "量能比 >= 1.4 且5日动量 > 0：+8；量能比 >= 1.4 且5日动量 < 0：-8",
    "当前版本暂未把60日动量纳入综合分；这是手工启发式评分，不是训练模型预测分。",
)
QLIB_SCORE_PROFILE_LABELS = {
    "balanced": "均衡量价",
    "trend_momentum": "趋势动量",
    "short_reversal": "短期反转",
    "low_volatility": "低波防守",
    "volume_breakout": "量价突破",
}


@dataclass(frozen=True)
class QlibWatchTarget:
    market: str
    symbol: str
    name: str
    start_date: str

    @property
    def qlib_symbol(self) -> str:
        return f"{self.market}{self.symbol}".lower()


@dataclass(frozen=True)
class QlibExportItem:
    market: str
    symbol: str
    name: str
    qlib_symbol: str
    csv_path: Path
    row_count: int
    source: str
    error: str = ""


@dataclass(frozen=True)
class QlibExportResult:
    qlib_repo_path: Path
    source_dir: Path
    qlib_dir: Path
    dump_command: str
    items: tuple[QlibExportItem, ...]

    @property
    def exported_count(self) -> int:
        return sum(1 for item in self.items if item.row_count > 0)


@dataclass(frozen=True)
class QlibFactorAnalysis:
    market: str
    symbol: str
    name: str
    qlib_symbol: str
    row_count: int
    source: str
    start_date: str
    end_date: str
    latest_close: float | None
    latest_change_percent: float | None
    return_5: float | None
    return_20: float | None
    return_60: float | None
    ma_5: float | None
    ma_20: float | None
    ma_60: float | None
    ma_20_distance: float | None
    volatility_20: float | None
    max_drawdown: float | None
    volume_ratio_5_20: float | None
    score: int | None
    signal: str
    model_status: str
    error: str = ""


@dataclass(frozen=True)
class QlibBacktestTrade:
    trigger_date: str
    trigger_score: int
    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float | None
    lot_size: int
    shares: int
    buy_cost: float
    sell_proceeds: float | None
    realized_profit: float | None
    realized_return_percent: float | None
    holding_days: int
    status: str


@dataclass(frozen=True)
class QlibBacktestPoint:
    date: str
    close: float
    score: int | None
    cash: float
    position_value: float
    equity: float
    action: str


@dataclass(frozen=True)
class QlibBacktestResult:
    market: str
    symbol: str
    name: str
    start_date: str
    end_date: str
    lot_size: int
    score_threshold: int
    score_profile: str
    take_profit_percent: float
    stop_loss_percent: float
    max_holding_days: int
    cost_rate: float
    capital_mode: str
    initial_capital: float
    total_invested: float
    total_fee: float
    max_capital_used: float
    final_equity: float
    total_profit: float
    total_return_percent: float
    trade_count: int
    closed_trade_count: int
    open_position_shares: int
    points: tuple[QlibBacktestPoint, ...]
    trades: tuple[QlibBacktestTrade, ...]
    rules: tuple[str, ...]
    source: str
    force_liquidate_end: bool = True
    error: str = ""


DEFAULT_QLIB_WATCH_TARGETS = (
    QlibWatchTarget(market="SZ", symbol="159278", name="机器人PH", start_date=QLIB_EXPORT_START_DATE),
    QlibWatchTarget(market="HK", symbol="03896", name="金山云", start_date=QLIB_EXPORT_START_DATE),
    QlibWatchTarget(market="HK", symbol="01810", name="小米集团", start_date=QLIB_EXPORT_START_DATE),
)


def backtest_qlib_one_lot_score_strategy(
    *,
    market: str,
    symbol: str,
    name: str,
    start_date: str,
    end_date: str | None = None,
    lot_size: int = 200,
    score_threshold: int = 84,
    score_profile: str = "balanced",
    take_profit_percent: float = 5.0,
    stop_loss_percent: float = 0.0,
    max_holding_days: int = 0,
    cost_rate: float = 0.01,
    force_liquidate_end: bool = True,
    refresh: bool = False,
) -> QlibBacktestResult:
    target = QlibWatchTarget(market=market.upper(), symbol=symbol, name=name, start_date=QLIB_EXPORT_START_DATE)
    item, all_rows = _load_target_daily_rows(target, refresh=refresh)
    return backtest_qlib_rows_one_lot_score_strategy(
        target=target,
        all_rows=all_rows,
        source=item.source,
        error=item.error,
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


def backtest_qlib_rows_one_lot_score_strategy(
    *,
    target: QlibWatchTarget,
    all_rows: tuple[AkshareStockHistoryRow, ...],
    source: str,
    error: str = "",
    start_date: str,
    end_date: str | None = None,
    lot_size: int = 200,
    score_threshold: int = 84,
    score_profile: str = "balanced",
    take_profit_percent: float = 5.0,
    stop_loss_percent: float = 0.0,
    max_holding_days: int = 0,
    cost_rate: float = 0.01,
    force_liquidate_end: bool = True,
) -> QlibBacktestResult:
    if not all_rows:
        return QlibBacktestResult(
            market=target.market,
            symbol=target.symbol,
            name=target.name,
            start_date=start_date,
            end_date=end_date or "",
            lot_size=lot_size,
            score_threshold=score_threshold,
            score_profile=normalize_score_profile(score_profile),
            take_profit_percent=take_profit_percent,
            stop_loss_percent=max(0, float(stop_loss_percent)),
            max_holding_days=max(0, int(max_holding_days)),
            cost_rate=cost_rate,
            capital_mode="unlimited",
            initial_capital=0,
            total_invested=0,
            total_fee=0,
            max_capital_used=0,
            final_equity=0,
            total_profit=0,
            total_return_percent=0,
            trade_count=0,
            closed_trade_count=0,
            open_position_shares=0,
            points=(),
            trades=(),
            rules=_backtest_strategy_rules(score_threshold=score_threshold, score_profile=score_profile, take_profit_percent=take_profit_percent, stop_loss_percent=stop_loss_percent, max_holding_days=max_holding_days, cost_rate=cost_rate, lot_size=lot_size, force_liquidate_end=force_liquidate_end),
            source=source,
            force_liquidate_end=force_liquidate_end,
            error=error or "没有可回测的日线数据",
        )

    start_iso = _normalize_iso_date(start_date) or start_date
    end_iso = _normalize_iso_date(end_date) if end_date else dt.date.today().isoformat()
    rows = tuple(row for row in all_rows if row.date <= end_iso)
    if not rows:
        rows = all_rows
    normalized_score_profile = normalize_score_profile(score_profile)
    score_by_date = _daily_factor_scores(rows, score_profile=normalized_score_profile)
    raw_points, trades, final_cash, final_position_shares, total_invested, total_fee, max_capital_used = _simulate_one_lot_score_strategy(
        rows=rows,
        score_by_date=score_by_date,
        start_date=start_iso,
        lot_size=max(1, int(lot_size)),
        score_threshold=int(score_threshold),
        take_profit_rate=max(0, float(take_profit_percent)) / 100,
        stop_loss_rate=max(0, float(stop_loss_percent)) / 100,
        max_holding_days=max(0, int(max_holding_days)),
        cost_rate=max(0, float(cost_rate)),
        force_liquidate_end=force_liquidate_end,
    )
    initial_capital = max_capital_used
    points = tuple(
        QlibBacktestPoint(
            date=point.date,
            close=point.close,
            score=point.score,
            cash=point.cash,
            position_value=point.position_value,
            equity=point.equity,
            action=point.action,
        )
        for point in raw_points
    )
    last_position_value = points[-1].position_value if points else 0
    final_equity = final_cash + last_position_value
    total_profit = final_equity
    total_return_percent = (total_profit / max_capital_used * 100) if max_capital_used else 0
    return QlibBacktestResult(
        market=target.market,
        symbol=target.symbol,
        name=target.name,
        start_date=start_iso,
        end_date=end_iso,
        lot_size=max(1, int(lot_size)),
        score_threshold=int(score_threshold),
        score_profile=normalized_score_profile,
        take_profit_percent=float(take_profit_percent),
        stop_loss_percent=max(0, float(stop_loss_percent)),
        max_holding_days=max(0, int(max_holding_days)),
        cost_rate=max(0, float(cost_rate)),
        capital_mode="unlimited",
        initial_capital=initial_capital,
        total_invested=total_invested,
        total_fee=total_fee,
        max_capital_used=max_capital_used,
        final_equity=final_equity,
        total_profit=total_profit,
        total_return_percent=total_return_percent,
        trade_count=len(trades),
        closed_trade_count=sum(1 for trade in trades if trade.status != "open"),
        open_position_shares=final_position_shares,
        points=points,
        trades=tuple(trades),
        rules=_backtest_strategy_rules(score_threshold=score_threshold, score_profile=normalized_score_profile, take_profit_percent=take_profit_percent, stop_loss_percent=stop_loss_percent, max_holding_days=max_holding_days, cost_rate=cost_rate, lot_size=lot_size, force_liquidate_end=force_liquidate_end),
        source=source,
        force_liquidate_end=force_liquidate_end,
        error=error,
    )


def analyze_qlib_daily_target(
    *,
    market: str,
    symbol: str,
    name: str,
    start_date: str,
    refresh: bool = False,
) -> QlibFactorAnalysis:
    target = QlibWatchTarget(market=market.upper(), symbol=symbol, name=name, start_date=start_date)
    item, rows = _load_target_daily_rows(target, refresh=refresh)
    if refresh and rows:
        _write_qlib_csv(item.csv_path, target=target, rows=rows)
    return _analyze_rows(target=target, item=item, rows=rows)


def export_qlib_daily_dataset(
    *,
    refresh: bool = True,
    targets: tuple[QlibWatchTarget, ...] = DEFAULT_QLIB_WATCH_TARGETS,
    qlib_repo_path: Path = QLIB_REPO_PATH,
) -> QlibExportResult:
    export_root = get_settings().data_dir / "stock" / "qlib"
    source_dir = export_root / "source" / "day"
    qlib_dir = export_root / "bin" / "day"
    source_dir.mkdir(parents=True, exist_ok=True)
    qlib_dir.mkdir(parents=True, exist_ok=True)

    items: list[QlibExportItem] = []
    for target in targets:
        item, rows = _load_target_daily_rows(target, source_dir=source_dir, refresh=refresh)
        if rows:
            _write_qlib_csv(item.csv_path, target=target, rows=rows)
        items.append(item)

    dump_script = qlib_repo_path / "scripts" / "dump_bin.py"
    dump_command = _format_dump_command(
        dump_script=dump_script,
        source_dir=source_dir,
        qlib_dir=qlib_dir,
    )
    return QlibExportResult(
        qlib_repo_path=qlib_repo_path,
        source_dir=source_dir,
        qlib_dir=qlib_dir,
        dump_command=dump_command,
        items=tuple(items),
    )


def serialize_qlib_export_result(result: QlibExportResult) -> dict[str, Any]:
    return {
        "qlib_repo_path": str(result.qlib_repo_path),
        "source_dir": str(result.source_dir),
        "qlib_dir": str(result.qlib_dir),
        "dump_command": result.dump_command,
        "exported_count": result.exported_count,
        "items": [
            {
                "market": item.market,
                "symbol": item.symbol,
                "name": item.name,
                "qlib_symbol": item.qlib_symbol,
                "csv_path": str(item.csv_path),
                "row_count": item.row_count,
                "source": item.source,
                "error": item.error,
            }
            for item in result.items
        ],
    }


def serialize_qlib_factor_analysis(analysis: QlibFactorAnalysis) -> dict[str, Any]:
    return {
        "market": analysis.market,
        "symbol": analysis.symbol,
        "name": analysis.name,
        "qlib_symbol": analysis.qlib_symbol,
        "row_count": analysis.row_count,
        "source": analysis.source,
        "start_date": analysis.start_date,
        "end_date": analysis.end_date,
        "latest_close": analysis.latest_close,
        "latest_change_percent": analysis.latest_change_percent,
        "return_5": analysis.return_5,
        "return_20": analysis.return_20,
        "return_60": analysis.return_60,
        "ma_5": analysis.ma_5,
        "ma_20": analysis.ma_20,
        "ma_60": analysis.ma_60,
        "ma_20_distance": analysis.ma_20_distance,
        "volatility_20": analysis.volatility_20,
        "max_drawdown": analysis.max_drawdown,
        "volume_ratio_5_20": analysis.volume_ratio_5_20,
        "score": analysis.score,
        "signal": analysis.signal,
        "model_status": analysis.model_status,
        "scoring_rules": list(QLIB_FACTOR_SCORE_RULES),
        "error": analysis.error,
    }


def serialize_qlib_backtest_result(result: QlibBacktestResult) -> dict[str, Any]:
    return {
        "market": result.market,
        "symbol": result.symbol,
        "name": result.name,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "lot_size": result.lot_size,
        "score_threshold": result.score_threshold,
        "score_profile": result.score_profile,
        "take_profit_percent": result.take_profit_percent,
        "stop_loss_percent": result.stop_loss_percent,
        "max_holding_days": result.max_holding_days,
        "cost_rate": result.cost_rate,
        "capital_mode": result.capital_mode,
        "initial_capital": result.initial_capital,
        "total_invested": result.total_invested,
        "total_fee": result.total_fee,
        "max_capital_used": result.max_capital_used,
        "final_equity": result.final_equity,
        "total_profit": result.total_profit,
        "total_return_percent": result.total_return_percent,
        "trade_count": result.trade_count,
        "closed_trade_count": result.closed_trade_count,
        "open_position_shares": result.open_position_shares,
        "source": result.source,
        "force_liquidate_end": result.force_liquidate_end,
        "rules": list(result.rules),
        "error": result.error,
        "points": [
            {
                "date": point.date,
                "close": point.close,
                "score": point.score,
                "cash": point.cash,
                "position_value": point.position_value,
                "equity": point.equity,
                "action": point.action,
            }
            for point in result.points
        ],
        "trades": [
            {
                "trigger_date": trade.trigger_date,
                "trigger_score": trade.trigger_score,
                "buy_date": trade.buy_date,
                "buy_price": trade.buy_price,
                "sell_date": trade.sell_date,
                "sell_price": trade.sell_price,
                "lot_size": trade.lot_size,
                "shares": trade.shares,
                "buy_cost": trade.buy_cost,
                "sell_proceeds": trade.sell_proceeds,
                "realized_profit": trade.realized_profit,
                "realized_return_percent": trade.realized_return_percent,
                "holding_days": trade.holding_days,
                "status": trade.status,
            }
            for trade in result.trades
        ],
    }


def _load_target_daily_rows(
    target: QlibWatchTarget,
    *,
    source_dir: Path | None = None,
    refresh: bool,
) -> tuple[QlibExportItem, tuple[AkshareStockHistoryRow, ...]]:
    if source_dir is None:
        source_dir = get_settings().data_dir / "stock" / "qlib" / "source" / "day"
    csv_path = source_dir / f"{target.qlib_symbol}.csv"
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
                _cache_daily_rows(target, rows)
        except Exception as exc:
            error = f"AKShare 刷新失败，尝试使用本地缓存：{exc}"

    if not rows:
        rows = _read_cached_daily_rows(target)
        source = "cache"

    if rows:
        return QlibExportItem(
            market=target.market,
            symbol=target.symbol,
            name=target.name,
            qlib_symbol=target.qlib_symbol,
            csv_path=csv_path,
            row_count=len(rows),
            source=source,
            error=error,
        ), rows

    return QlibExportItem(
        market=target.market,
        symbol=target.symbol,
        name=target.name,
        qlib_symbol=target.qlib_symbol,
        csv_path=csv_path,
        row_count=0,
        source=source,
        error=error or "没有可导出的 AKShare 日线数据",
    ), rows


def _analyze_rows(
    *,
    target: QlibWatchTarget,
    item: QlibExportItem,
    rows: tuple[AkshareStockHistoryRow, ...],
) -> QlibFactorAnalysis:
    closes = [float(row.close) for row in rows if row.close is not None and math.isfinite(float(row.close))]
    volumes = [float(row.volume) for row in rows if row.volume is not None and math.isfinite(float(row.volume))]
    latest_close = closes[-1] if closes else None
    latest_change_percent = rows[-1].change_percent if rows else None
    return_5 = _period_return(closes, 5)
    return_20 = _period_return(closes, 20)
    return_60 = _period_return(closes, 60)
    ma_5 = _moving_average(closes, 5)
    ma_20 = _moving_average(closes, 20)
    ma_60 = _moving_average(closes, 60)
    ma_20_distance = (latest_close / ma_20 - 1) * 100 if latest_close and ma_20 else None
    volatility_20 = _annualized_volatility(closes, 20)
    max_drawdown = _max_drawdown(closes)
    volume_ratio_5_20 = _volume_ratio(volumes)
    score = _factor_score(
        return_5=return_5,
        return_20=return_20,
        return_60=return_60,
        ma_20_distance=ma_20_distance,
        volatility_20=volatility_20,
        max_drawdown=max_drawdown,
        volume_ratio_5_20=volume_ratio_5_20,
    ) if closes else None
    signal = _signal_label(score)
    return QlibFactorAnalysis(
        market=target.market,
        symbol=target.symbol,
        name=target.name,
        qlib_symbol=target.qlib_symbol,
        row_count=len(rows),
        source=item.source,
        start_date=rows[0].date if rows else "",
        end_date=rows[-1].date if rows else "",
        latest_close=latest_close,
        latest_change_percent=latest_change_percent,
        return_5=return_5,
        return_20=return_20,
        return_60=return_60,
        ma_5=ma_5,
        ma_20=ma_20,
        ma_60=ma_60,
        ma_20_distance=ma_20_distance,
        volatility_20=volatility_20,
        max_drawdown=max_drawdown,
        volume_ratio_5_20=volume_ratio_5_20,
        score=score,
        signal=signal,
        model_status="Qlib 日线数据集因子摘要，暂未接入训练模型预测",
        error=item.error,
    )


def _cache_daily_rows(target: QlibWatchTarget, rows: tuple[AkshareStockHistoryRow, ...]) -> None:
    history_target = MarketHistoryTarget(
        market=target.market,
        symbol=target.symbol,
        provider_code=target.symbol,
        name=target.name,
        sources=("akshare:qlib",),
        first_trade_date=target.start_date,
        start_date=target.start_date,
        end_date=rows[-1].date if rows else "",
    )
    payload_rows = [
        {
            "time_key": row.date,
            "date": row.date,
            "open": row.open,
            "close": row.close,
            "high": row.high,
            "low": row.low,
            "volume": row.volume,
            "turnover": row.amount,
            "turnover_rate": row.turnover_rate,
            "change_rate": row.change_percent,
            "change_amount": row.change_amount,
            "amplitude": row.amplitude,
            "raw_symbol": row.symbol,
        }
        for row in rows
    ]
    with connect_market_data_db() as conn:
        upsert_kline_rows(
            conn,
            provider=MARKET_DATA_PROVIDER_AKSHARE,
            target=history_target,
            ktype="daily",
            autype="none",
            rows=payload_rows,
            provisional_date=history_target.end_date,
        )


def _period_return(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return (values[-1] / values[-periods - 1] - 1) * 100


def _moving_average(values: list[float], periods: int) -> float | None:
    if len(values) < periods:
        return None
    return sum(values[-periods:]) / periods


def _annualized_volatility(values: list[float], periods: int) -> float | None:
    if len(values) <= periods:
        return None
    returns = []
    for index in range(len(values) - periods, len(values)):
        previous = values[index - 1]
        current = values[index]
        if previous:
            returns.append(current / previous - 1)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(252) * 100


def _max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            drawdown = min(drawdown, value / peak - 1)
    return drawdown * 100


def _volume_ratio(values: list[float]) -> float | None:
    if len(values) < 20:
        return None
    average_5 = sum(values[-5:]) / 5
    average_20 = sum(values[-20:]) / 20
    return average_5 / average_20 if average_20 else None


def _factor_score(
    *,
    return_5: float | None,
    return_20: float | None,
    ma_20_distance: float | None,
    volatility_20: float | None,
    max_drawdown: float | None,
    volume_ratio_5_20: float | None,
    return_60: float | None = None,
    score_profile: str = "balanced",
) -> int:
    profile = normalize_score_profile(score_profile)
    if profile == "trend_momentum":
        return _trend_momentum_score(
            return_5=return_5,
            return_20=return_20,
            return_60=return_60,
            ma_20_distance=ma_20_distance,
            volatility_20=volatility_20,
            volume_ratio_5_20=volume_ratio_5_20,
        )
    if profile == "short_reversal":
        return _short_reversal_score(
            return_5=return_5,
            return_20=return_20,
            ma_20_distance=ma_20_distance,
            volatility_20=volatility_20,
            max_drawdown=max_drawdown,
            volume_ratio_5_20=volume_ratio_5_20,
        )
    if profile == "low_volatility":
        return _low_volatility_score(
            return_20=return_20,
            ma_20_distance=ma_20_distance,
            volatility_20=volatility_20,
            max_drawdown=max_drawdown,
        )
    if profile == "volume_breakout":
        return _volume_breakout_score(
            return_5=return_5,
            return_20=return_20,
            ma_20_distance=ma_20_distance,
            volatility_20=volatility_20,
            volume_ratio_5_20=volume_ratio_5_20,
        )
    score = 50
    if return_5 is not None:
        score += 12 if return_5 > 0 else -8
    if return_20 is not None:
        score += 12 if return_20 > 0 else -10
    if ma_20_distance is not None:
        if -8 <= ma_20_distance <= 3:
            score += 10
        elif ma_20_distance > 10:
            score -= 10
        elif ma_20_distance < -15:
            score -= 6
    if max_drawdown is not None:
        if max_drawdown < -25:
            score -= 8
        elif -18 <= max_drawdown <= -5:
            score += 6
    if volatility_20 is not None and volatility_20 > 70:
        score -= 8
    if volume_ratio_5_20 is not None:
        if volume_ratio_5_20 >= 1.4 and (return_5 or 0) > 0:
            score += 8
        elif volume_ratio_5_20 >= 1.4 and (return_5 or 0) < 0:
            score -= 8
    return max(0, min(100, round(score)))


def normalize_score_profile(value: str | None) -> str:
    text = str(value or "balanced").strip()
    return text if text in QLIB_SCORE_PROFILE_LABELS else "balanced"


def _clamp_score(score: float) -> int:
    return max(0, min(100, round(score)))


def _trend_momentum_score(
    *,
    return_5: float | None,
    return_20: float | None,
    return_60: float | None,
    ma_20_distance: float | None,
    volatility_20: float | None,
    volume_ratio_5_20: float | None,
) -> int:
    score = 50
    if return_20 is not None:
        score += 18 if return_20 > 0 else -14
    if return_60 is not None:
        score += 16 if return_60 > 0 else -10
    if return_5 is not None:
        score += 6 if return_5 > 0 else -4
    if ma_20_distance is not None:
        if 0 <= ma_20_distance <= 12:
            score += 10
        elif ma_20_distance > 18:
            score -= 10
        elif ma_20_distance < -8:
            score -= 8
    if volume_ratio_5_20 is not None and volume_ratio_5_20 >= 1.2 and (return_20 or 0) > 0:
        score += 8
    if volatility_20 is not None and volatility_20 > 85:
        score -= 10
    return _clamp_score(score)


def _short_reversal_score(
    *,
    return_5: float | None,
    return_20: float | None,
    ma_20_distance: float | None,
    volatility_20: float | None,
    max_drawdown: float | None,
    volume_ratio_5_20: float | None,
) -> int:
    score = 50
    if return_5 is not None:
        score += 14 if return_5 < 0 else -6
    if return_20 is not None:
        score += 10 if return_20 > -12 else -10
    if ma_20_distance is not None:
        if -12 <= ma_20_distance <= -2:
            score += 16
        elif ma_20_distance > 8:
            score -= 12
        elif ma_20_distance < -22:
            score -= 8
    if max_drawdown is not None:
        if -22 <= max_drawdown <= -5:
            score += 8
        elif max_drawdown < -35:
            score -= 12
    if volume_ratio_5_20 is not None and volume_ratio_5_20 >= 1.6 and (return_5 or 0) < 0:
        score += 4
    if volatility_20 is not None and volatility_20 > 95:
        score -= 12
    return _clamp_score(score)


def _low_volatility_score(
    *,
    return_20: float | None,
    ma_20_distance: float | None,
    volatility_20: float | None,
    max_drawdown: float | None,
) -> int:
    score = 50
    if return_20 is not None:
        score += 10 if return_20 > 0 else -8
    if volatility_20 is not None:
        if volatility_20 < 45:
            score += 16
        elif volatility_20 > 70:
            score -= 16
    if max_drawdown is not None:
        if max_drawdown > -15:
            score += 12
        elif max_drawdown < -25:
            score -= 16
    if ma_20_distance is not None:
        if -5 <= ma_20_distance <= 5:
            score += 8
        elif abs(ma_20_distance) > 15:
            score -= 8
    return _clamp_score(score)


def _volume_breakout_score(
    *,
    return_5: float | None,
    return_20: float | None,
    ma_20_distance: float | None,
    volatility_20: float | None,
    volume_ratio_5_20: float | None,
) -> int:
    score = 50
    if return_5 is not None:
        score += 10 if return_5 > 0 else -10
    if return_20 is not None:
        score += 8 if return_20 > 0 else -6
    if volume_ratio_5_20 is not None:
        if volume_ratio_5_20 >= 1.8 and (return_5 or 0) > 0:
            score += 18
        elif volume_ratio_5_20 >= 1.3 and (return_5 or 0) > 0:
            score += 10
        elif volume_ratio_5_20 >= 1.5 and (return_5 or 0) < 0:
            score -= 10
    if ma_20_distance is not None:
        if 0 <= ma_20_distance <= 10:
            score += 8
        elif ma_20_distance > 18:
            score -= 10
    if volatility_20 is not None and volatility_20 > 90:
        score -= 10
    return _clamp_score(score)


def _signal_label(score: int | None) -> str:
    if score is None:
        return "数据不足"
    if score >= 70:
        return "偏积极"
    if score <= 40:
        return "偏谨慎"
    return "中性观察"


def _normalize_iso_date(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _daily_factor_scores(rows: tuple[AkshareStockHistoryRow, ...], *, score_profile: str = "balanced") -> dict[str, int | None]:
    scores: dict[str, int | None] = {}
    closes: list[float] = []
    volumes: list[float] = []
    returns: list[float] = []
    return_sum_20 = 0.0
    return_square_sum_20 = 0.0
    peak_close: float | None = None
    max_drawdown: float | None = None
    for row in rows:
        close = _finite_float(row.close)
        volume = _finite_float(row.volume)
        if close is None:
            scores[row.date] = None
            continue
        previous_close = closes[-1] if closes else None
        closes.append(close)
        volume_value = volume or 0
        volumes.append(volume_value)
        if previous_close:
            daily_return = close / previous_close - 1
            returns.append(daily_return)
            return_sum_20 += daily_return
            return_square_sum_20 += daily_return ** 2
            if len(returns) > 20:
                removed_return = returns[-21]
                return_sum_20 -= removed_return
                return_square_sum_20 -= removed_return ** 2
        peak_close = close if peak_close is None else max(peak_close, close)
        current_drawdown = (close / peak_close - 1) * 100 if peak_close else 0
        max_drawdown = current_drawdown if max_drawdown is None else min(max_drawdown, current_drawdown)
        return_5 = _period_return(closes, 5)
        return_20 = _period_return(closes, 20)
        return_60 = _period_return(closes, 60)
        ma_20 = _moving_average(closes, 20)
        ma_20_distance = (close / ma_20 - 1) * 100 if ma_20 else None
        volatility_20 = None
        if len(returns) >= 20:
            mean = return_sum_20 / 20
            variance = (return_square_sum_20 - 20 * mean ** 2) / 19
            volatility_20 = math.sqrt(max(0, variance)) * math.sqrt(252) * 100
        volume_ratio_5_20 = _volume_ratio(volumes)
        scores[row.date] = _factor_score(
            return_5=return_5,
            return_20=return_20,
            ma_20_distance=ma_20_distance,
            volatility_20=volatility_20,
            max_drawdown=max_drawdown,
            volume_ratio_5_20=volume_ratio_5_20,
            return_60=return_60,
            score_profile=score_profile,
        )
    return scores


def _simulate_one_lot_score_strategy(
    *,
    rows: tuple[AkshareStockHistoryRow, ...],
    score_by_date: dict[str, int | None],
    start_date: str,
    lot_size: int,
    score_threshold: int,
    take_profit_rate: float,
    stop_loss_rate: float,
    max_holding_days: int,
    cost_rate: float,
    force_liquidate_end: bool,
) -> tuple[list[QlibBacktestPoint], list[QlibBacktestTrade], float, int, float, float, float]:
    points: list[QlibBacktestPoint] = []
    trades: list[QlibBacktestTrade] = []
    open_positions: list[dict[str, Any]] = []
    pending_buys: list[tuple[str, int]] = []
    cash = 0.0
    total_invested = 0.0
    total_fee = 0.0
    max_capital_used = 0.0

    for index, row in enumerate(rows):
        close = _finite_float(row.close)
        open_price = _finite_float(row.open)
        high = _finite_float(row.high)
        low = _finite_float(row.low)
        if close is None:
            continue

        actions: list[str] = []
        if row.date >= start_date and pending_buys and open_price is not None and open_price > 0:
            for trigger_date, trigger_score in pending_buys:
                buy_gross = open_price * lot_size
                buy_fee = buy_gross * cost_rate
                buy_cost = buy_gross + buy_fee
                cash -= buy_cost
                total_invested += buy_cost
                total_fee += buy_fee
                open_positions.append(
                    {
                        "trigger_date": trigger_date,
                        "trigger_score": trigger_score,
                        "buy_date": row.date,
                        "buy_index": index,
                        "buy_price": open_price,
                        "buy_cost": buy_cost,
                        "target_price": open_price * (1 + take_profit_rate),
                        "stop_price": open_price * (1 - stop_loss_rate) if stop_loss_rate > 0 else None,
                    }
                )
                actions.append("买入")
            pending_buys = []
            max_capital_used = max(max_capital_used, sum(float(position["buy_cost"]) for position in open_positions))

        remaining_positions: list[dict[str, Any]] = []
        for position in open_positions:
            target_price = float(position["target_price"])
            stop_price = _finite_float(position.get("stop_price"))
            buy_index = int(position["buy_index"])
            holding_days = max(0, index - buy_index)
            sell_price: float | None = None
            trade_status = "closed"
            if stop_price is not None and low is not None and low <= stop_price:
                sell_price = stop_price
                trade_status = "stop_loss"
            elif high is not None and high >= target_price:
                sell_price = target_price
                trade_status = "closed"
            elif max_holding_days > 0 and holding_days >= max_holding_days:
                sell_price = close
                trade_status = "time_exit"
            if sell_price is not None:
                sell_gross = sell_price * lot_size
                sell_fee = sell_gross * cost_rate
                sell_proceeds = sell_gross - sell_fee
                cash += sell_proceeds
                total_fee += sell_fee
                realized_profit = sell_proceeds - float(position["buy_cost"])
                trades.append(
                    QlibBacktestTrade(
                        trigger_date=str(position["trigger_date"]),
                        trigger_score=int(position["trigger_score"]),
                        buy_date=str(position["buy_date"]),
                        buy_price=float(position["buy_price"]),
                        sell_date=row.date,
                        sell_price=sell_price,
                        lot_size=lot_size,
                        shares=lot_size,
                        buy_cost=float(position["buy_cost"]),
                        sell_proceeds=sell_proceeds,
                        realized_profit=realized_profit,
                        realized_return_percent=realized_profit / float(position["buy_cost"]) * 100 if position["buy_cost"] else None,
                        holding_days=holding_days,
                        status=trade_status,
                    )
                )
                actions.append({"closed": "止盈", "stop_loss": "止损", "time_exit": "到期"}[trade_status])
            else:
                remaining_positions.append(position)
        open_positions = remaining_positions

        open_cost = sum(float(position["buy_cost"]) for position in open_positions)
        max_capital_used = max(max_capital_used, open_cost)
        position_value = len(open_positions) * lot_size * close
        score = score_by_date.get(row.date)
        if row.date >= start_date and score is not None and score >= score_threshold and index + 1 < len(rows):
            pending_buys.append((row.date, score))
            actions.append("触发")
        if row.date >= start_date:
            points.append(
                QlibBacktestPoint(
                    date=row.date,
                    close=close,
                    score=score,
                    cash=cash,
                    position_value=position_value,
                    equity=cash + position_value,
                    action="/".join(actions),
                )
            )

    final_close = _finite_float(rows[-1].close) if rows else None
    if force_liquidate_end and open_positions and final_close is not None:
        final_date = rows[-1].date
        final_action = "年末平仓"
        for position in open_positions:
            buy_index = int(position["buy_index"])
            sell_gross = final_close * lot_size
            sell_fee = sell_gross * cost_rate
            sell_proceeds = sell_gross - sell_fee
            cash += sell_proceeds
            total_fee += sell_fee
            realized_profit = sell_proceeds - float(position["buy_cost"])
            trades.append(
                QlibBacktestTrade(
                    trigger_date=str(position["trigger_date"]),
                    trigger_score=int(position["trigger_score"]),
                    buy_date=str(position["buy_date"]),
                    buy_price=float(position["buy_price"]),
                    sell_date=final_date,
                    sell_price=final_close,
                    lot_size=lot_size,
                    shares=lot_size,
                    buy_cost=float(position["buy_cost"]),
                    sell_proceeds=sell_proceeds,
                    realized_profit=realized_profit,
                    realized_return_percent=realized_profit / float(position["buy_cost"]) * 100 if position["buy_cost"] else None,
                    holding_days=max(0, len(rows) - 1 - buy_index),
                    status="forced_closed",
                )
            )
        open_positions = []
        if points:
            last = points[-1]
            points[-1] = QlibBacktestPoint(
                date=last.date,
                close=last.close,
                score=last.score,
                cash=cash,
                position_value=0,
                equity=cash,
                action="/".join(item for item in (last.action, final_action) if item),
            )
    else:
        for position in open_positions:
            buy_index = int(position["buy_index"])
            unrealized_sell_price = final_close
            unrealized_proceeds = unrealized_sell_price * lot_size * (1 - cost_rate) if unrealized_sell_price is not None else None
            unrealized_profit = unrealized_proceeds - float(position["buy_cost"]) if unrealized_proceeds is not None else None
            trades.append(
                QlibBacktestTrade(
                    trigger_date=str(position["trigger_date"]),
                    trigger_score=int(position["trigger_score"]),
                    buy_date=str(position["buy_date"]),
                    buy_price=float(position["buy_price"]),
                    sell_date="",
                    sell_price=None,
                    lot_size=lot_size,
                    shares=lot_size,
                    buy_cost=float(position["buy_cost"]),
                    sell_proceeds=unrealized_proceeds,
                    realized_profit=unrealized_profit,
                    realized_return_percent=unrealized_profit / float(position["buy_cost"]) * 100 if unrealized_profit is not None and position["buy_cost"] else None,
                    holding_days=max(0, len(rows) - 1 - buy_index),
                    status="open",
                )
            )

    return points, trades, cash, len(open_positions) * lot_size, total_invested, total_fee, max_capital_used


def _backtest_strategy_rules(
    *,
    score_threshold: int,
    score_profile: str,
    take_profit_percent: float,
    stop_loss_percent: float,
    max_holding_days: int,
    cost_rate: float,
    lot_size: int,
    force_liquidate_end: bool,
) -> tuple[str, ...]:
    profile_label = QLIB_SCORE_PROFILE_LABELS.get(normalize_score_profile(score_profile), "均衡量价")
    rules = [
        f"从回测开始日后，每个交易日收盘后按“{profile_label}”模型计算综合分；综合分 >= {int(score_threshold)} 时，次一交易日按开盘价买入一手。",
        f"一手按 {int(lot_size)} 股计算；允许无限资金，因此每次信号都会新增一手，不因已有持仓跳过。",
        f"每一手独立持仓；日内最高价达到买入价上涨 {float(take_profit_percent):g}% 时，按目标价卖出。",
        f"买入和卖出均按单边成本 {float(cost_rate) * 100:g}% 扣减；日线数据无法还原真实盘中成交顺序。",
    ]
    if float(stop_loss_percent) > 0:
        rules.append(f"日内最低价触及买入价下跌 {float(stop_loss_percent):g}% 时止损；若同日止盈和止损都触发，保守按止损先发生。")
    if int(max_holding_days) > 0:
        rules.append(f"持仓达到 {int(max_holding_days)} 个交易日仍未止盈/止损，则按当日收盘价退出。")
    if force_liquidate_end:
        rules.append("回测结束日按最后一个可用收盘价强制平掉所有未卖出持仓，最终结果只看现金。")
    else:
        rules.append("回测结束日不强制平仓，未卖出持仓按期末市值计入权益。")
    rules.append("initial_capital 在无限资金模型下表示最大资金占用，用于收益率参考，不是下单约束。")
    return tuple(rules)


def _finite_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
            (
                MARKET_DATA_PROVIDER_AKSHARE,
                target.market,
                target.symbol,
                target.start_date,
            ),
        ).fetchall()
    cached_rows: list[AkshareStockHistoryRow] = []
    for row in rows:
        open_price, close_price, high_price, low_price = _normalize_ohlc_prices(
            row["open"],
            row["close"],
            row["high"],
            row["low"],
        )
        cached_rows.append(
            AkshareStockHistoryRow(
                date=str(row["time_key"] or "")[:10],
                symbol=target.symbol,
                open=open_price,
                close=close_price,
                high=high_price,
                low=low_price,
                volume=row["volume"],
                amount=row["turnover"],
                amplitude=None,
                change_percent=row["change_rate"],
                change_amount=None,
                turnover_rate=row["turnover_rate"],
            )
        )
    return tuple(cached_rows)


def _write_qlib_csv(csv_path: Path, *, target: QlibWatchTarget, rows: tuple[AkshareStockHistoryRow, ...]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=("date", "symbol", "open", "close", "high", "low", "volume", "amount", "change"),
        )
        writer.writeheader()
        for row in rows:
            open_price, close_price, high_price, low_price = _normalize_ohlc_prices(
                row.open,
                row.close,
                row.high,
                row.low,
            )
            writer.writerow(
                {
                    "date": row.date,
                    "symbol": target.qlib_symbol.upper(),
                    "open": _number_or_empty(open_price),
                    "close": _number_or_empty(close_price),
                    "high": _number_or_empty(high_price),
                    "low": _number_or_empty(low_price),
                    "volume": _number_or_empty(row.volume),
                    "amount": _number_or_empty(row.amount),
                    "change": _number_or_empty(row.change_percent),
                }
            )


def _format_dump_command(*, dump_script: Path, source_dir: Path, qlib_dir: Path) -> str:
    return subprocess.list2cmdline(
        [
            "uv",
            "run",
            "python",
            str(dump_script),
            "dump_all",
            "--data_path",
            str(source_dir),
            "--qlib_dir",
            str(qlib_dir),
            "--freq",
            "day",
            "--exclude_fields",
            "date,symbol",
        ]
    )


def _number_or_empty(value: float | None) -> float | str:
    return "" if value is None else float(value)
