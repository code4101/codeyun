from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from backend.core.settings import get_settings

from .akshare_market import AkshareStockHistoryRow, fetch_akshare_stock_history
from .hkex_board_lot import load_hkex_board_lots
from .index_benchmark import IndexBenchmark, load_index_benchmarks, serialize_index_benchmark
from .market_data import MARKET_DATA_PROVIDER_AKSHARE, connect_market_data_db
from .qlib_bridge import (
    QLIB_EXPORT_START_DATE,
    QLIB_FACTOR_SCORE_RULES,
    QlibFactorAnalysis,
    QlibBacktestResult,
    QlibWatchTarget,
    _analyze_rows,
    backtest_qlib_one_lot_score_strategy,
    backtest_qlib_rows_one_lot_score_strategy,
    _cache_daily_rows,
    serialize_qlib_backtest_result,
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
    take_profit_percent: float
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


def backtest_hk_pool_one_lot_score(
    *,
    refresh: bool = False,
    limit: int | None = None,
    start_date: str = "2025-01-01",
    end_date: str | None = None,
    score_threshold: int = 84,
    take_profit_percent: float = 5.0,
    cost_rate: float = 0.01,
    force_liquidate_end: bool = True,
) -> QlibPoolBacktestResult:
    cache_path = get_settings().data_dir / "stock" / "qlib" / "hk_pool_backtest_one_lot_score.json"
    cache_key = {
        "start_date": start_date,
        "end_date": end_date or dt.date.today().isoformat(),
        "score_threshold": int(score_threshold),
        "take_profit_percent": float(take_profit_percent),
        "cost_rate": float(cost_rate),
        "force_liquidate_end": bool(force_liquidate_end),
        "limit": limit,
    }
    if not refresh:
        cached = _read_hk_pool_backtest_cache(cache_path, cache_key)
        if cached is not None:
            return cached

    targets = list_hk_screen_targets(limit=limit)
    board_lots = load_hkex_board_lots(refresh=refresh)
    items: list[QlibPoolBacktestItem] = []
    with connect_market_data_db() as conn:
        for target in targets:
            lot_size = board_lots.get(target.symbol)
            if not lot_size:
                items.append(QlibPoolBacktestItem(target=target, lot_size=None, result=None, error="缺少港交所一手股数字段"))
                continue
            rows = _read_cached_daily_rows_with_conn(conn, target.qlib_target)
            result = backtest_qlib_rows_one_lot_score_strategy(
                target=target.qlib_target,
                all_rows=rows,
                source="cache",
                start_date=start_date,
                end_date=end_date,
                lot_size=lot_size,
                score_threshold=score_threshold,
                take_profit_percent=take_profit_percent,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
            )
            if result.error or not result.points:
                items.append(QlibPoolBacktestItem(target=target, lot_size=lot_size, result=result, error=result.error or "本地日线数据不足"))
                continue
            items.append(QlibPoolBacktestItem(target=target, lot_size=lot_size, result=result, error=result.error))

    ranked_items = sorted(
        items,
        key=lambda item: (
            item.result is not None,
            item.result.total_profit if item.result is not None else float("-inf"),
            item.result.trade_count if item.result is not None else -1,
        ),
        reverse=True,
    )
    result = QlibPoolBacktestResult(
        pool=HK_POOL,
        source="cache:market_kline; hkex:board_lot",
        target_count=len(targets),
        tested_count=sum(1 for item in items if item.result is not None and not item.error and item.result.points),
        skipped_count=sum(1 for item in items if item.result is None or bool(item.error)),
        start_date=start_date,
        end_date=end_date or dt.date.today().isoformat(),
        score_threshold=int(score_threshold),
        take_profit_percent=float(take_profit_percent),
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
    _write_hk_pool_backtest_cache(cache_path, cache_key, result)
    return result


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
        "take_profit_percent": result.take_profit_percent,
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
        take_profit_percent=float(payload.get("take_profit_percent") or cache_key["take_profit_percent"]),
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
        "take_profit_percent": result.take_profit_percent,
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
        take_profit_percent=float(row.get("take_profit_percent") or 0),
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
