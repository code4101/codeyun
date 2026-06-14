from __future__ import annotations

import datetime as dt
import csv
import json
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview
from backend.core.settings import get_settings
from backend.core.stock import (
    EastmoneyTradeError,
    analyze_qlib_daily_target,
    backtest_qlib_one_lot_score_strategy,
    backtest_hk_pool_one_lot_score,
    build_qlib_rotation_strategy_candidates,
    build_qlib_strategy_candidates,
    compute_cross_asset_etf_canary_rotation,
    compute_hk_connect_momentum_review,
    export_qlib_daily_dataset,
    fetch_akshare_etf_intraday,
    fetch_akshare_stock_history,
    get_market_data_db_path,
    get_latest_asset_snapshot,
    import_mobile_trade_detail_record,
    get_strategy_research_item,
    list_strategy_research_backlog,
    list_latest_market_quotes,
    list_fund_flow_categories,
    list_fund_flow_filter_options,
    list_fund_flow_records,
    list_latest_position_snapshots,
    list_sync_runs,
    list_trade_records,
    list_strategy_research_items,
    open_trade_account_page,
    read_trade_snapshot,
    refresh_market_quotes_from_akshare,
    refresh_eastmoney_sheet_workbook,
    serialize_quote_item,
    serialize_quote_refresh_result,
    serialize_akshare_etf_intraday,
    serialize_akshare_stock_history,
    serialize_etf_rotation_backtest_result,
    serialize_qlib_export_result,
    serialize_qlib_factor_analysis,
    serialize_qlib_backtest_result,
    serialize_qlib_pool_backtest_result,
    serialize_qlib_rotation_strategy_search_result,
    serialize_hk_connect_momentum_review_result,
    screen_hk_pool,
    serialize_qlib_screen_result,
    serialize_qlib_strategy_search_result,
    search_hk_pool_ranked_rotation_strategies,
    search_hk_pool_one_lot_score_strategies,
    snapshot_to_dict,
    sync_trade_data,
)
from backend.core.stock.qlib_screening import (
    _read_hk_pool_backtest_cache,
    _hk_pool_backtest_cache_path,
    _read_strategy_search_cache,
    _strategy_search_cache_key,
    _write_hk_pool_backtest_cache,
    _write_strategy_search_cache,
)
from backend.core.stock.market_data import (
    MARKET_DATA_PROVIDER_AKSHARE,
    MarketHistoryTarget,
    connect_market_data_db,
    normalize_autype,
    normalize_ktype,
    normalize_market_code,
    upsert_intraday_rows,
    upsert_kline_rows,
)
from backend.core.stock.akshare_market import (
    AkshareEtfIntraday,
    AkshareEtfIntradayRow,
    AkshareStockHistory,
    AkshareStockHistoryRow,
    _aggregate_history_rows,
    _normalize_ohlc_prices,
)
from backend.core.stock.eastmoney_ocr import parse_mobile_trade_detail_from_ocr_document
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.eastmoney"))],
)


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_qlib_source_history(
    *,
    market: str,
    symbol: str,
    name: str,
    period: str,
    start_date: str,
    end_date: str | None,
    adjust: str,
) -> AkshareStockHistory | None:
    normalized_market = (market or "").strip().upper()
    normalized_symbol = (symbol or "").strip()
    if not normalized_market or not normalized_symbol:
        return None

    csv_path = get_settings().data_dir / "stock" / "qlib" / "source" / "day" / f"{normalized_market.lower()}{normalized_symbol}.csv"
    if not csv_path.exists():
        return None

    start_iso = akshare_date_to_iso(start_date)
    end_iso = akshare_date_to_iso(end_date) if end_date else ""
    rows: list[AkshareStockHistoryRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        for record in csv.DictReader(file):
            row_date = str(record.get("date") or "")
            if start_iso and row_date < start_iso:
                continue
            if end_iso and row_date > end_iso:
                continue
            open_price, close_price, high_price, low_price = _normalize_ohlc_prices(
                _float_or_none(record.get("open")),
                _float_or_none(record.get("close")),
                _float_or_none(record.get("high")),
                _float_or_none(record.get("low")),
            )
            rows.append(
                AkshareStockHistoryRow(
                    date=row_date,
                    symbol=normalized_symbol,
                    open=open_price,
                    close=close_price,
                    high=high_price,
                    low=low_price,
                    volume=_float_or_none(record.get("volume")),
                    amount=_float_or_none(record.get("amount")),
                    amplitude=None,
                    change_percent=_float_or_none(record.get("change")),
                    change_amount=None,
                    turnover_rate=None,
                )
            )
    normalized_period = normalize_ktype(period)
    tuple_rows = tuple(rows)
    if normalized_period in {"weekly", "monthly", "quarterly", "yearly"}:
        tuple_rows = _aggregate_history_rows(tuple_rows, normalized_symbol, normalized_period)
    return AkshareStockHistory(
        provider="qlib-source-cache",
        market=normalized_market,
        symbol=normalized_symbol,
        name=name.strip() or normalized_symbol,
        period=normalized_period,
        adjust=adjust,
        start_date=start_iso,
        end_date=end_iso,
        rows=tuple_rows,
    )

_hk_pool_backtest_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hk-pool-backtest-api")
_hk_pool_backtest_lock = Lock()
_hk_pool_backtest_jobs: dict[str, Future] = {}
_hk_pool_strategy_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hk-pool-strategy-api")
_hk_pool_strategy_lock = Lock()
_hk_pool_strategy_jobs: dict[str, Future] = {}


class EastmoneySyncRequest(BaseModel):
    start_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.get("/strategy-research")
def get_eastmoney_strategy_research_catalog(
    family: str | None = Query(default=None, max_length=80),
    status: str | None = Query(default=None, max_length=80),
    market: str | None = Query(default=None, max_length=20),
    min_priority: int | None = Query(default=None, ge=1, le=9),
):
    try:
        return list_strategy_research_items(
            family=family,
            status=status,
            market=market,
            min_priority=min_priority,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取策略研究库失败：{exc}") from exc


@router.get("/strategy-research/{strategy_id}")
def get_eastmoney_strategy_research_item(strategy_id: str):
    try:
        item = get_strategy_research_item(strategy_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取策略研究条目失败：{exc}") from exc
    if item is None:
        raise HTTPException(status_code=404, detail="策略研究条目不存在")
    return item


@router.get("/strategy-research-backlog")
def get_eastmoney_strategy_research_backlog(
    max_priority: int = Query(default=3, ge=1, le=9),
):
    try:
        return list_strategy_research_backlog(max_priority=max_priority)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取策略研究队列失败：{exc}") from exc


@router.post("/qlib/export")
def export_eastmoney_qlib_dataset(refresh: bool = Query(default=True)):
    try:
        return serialize_qlib_export_result(export_qlib_daily_dataset(refresh=refresh))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"导出 Qlib 数据失败：{exc}") from exc


@router.get("/qlib/analysis")
def get_eastmoney_qlib_analysis(
    market: str = Query(default="SZ", max_length=8),
    symbol: str = Query(default="159278", min_length=1, max_length=12),
    name: str = Query(default="机器人PH", max_length=80),
    start_date: str = Query(default="2025-08-12"),
    refresh: bool = Query(default=False),
):
    try:
        return serialize_qlib_factor_analysis(
            analyze_qlib_daily_target(
                market=market,
                symbol=symbol,
                name=name,
                start_date=start_date,
                refresh=refresh,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Qlib 分析失败：{exc}") from exc


@router.get("/qlib/screen/hk-pool")
def get_eastmoney_qlib_hk_pool_screen(
    refresh: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=5000),
    start_date: str = Query(default="1990-01-01"),
):
    try:
        return serialize_qlib_screen_result(
            screen_hk_pool(
                refresh=refresh,
                limit=limit,
                start_date=start_date,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取港股股票池评分失败：{exc}") from exc


@router.get("/qlib/backtest/one-lot-score")
def get_eastmoney_qlib_one_lot_score_backtest(
    market: str = Query(default="HK", max_length=8),
    symbol: str = Query(default="01810", min_length=1, max_length=12),
    name: str = Query(default="小米集团", max_length=80),
    start_date: str = Query(default="2025-01-01"),
    end_date: str | None = Query(default=None),
    lot_size: int = Query(default=200, ge=1, le=1000000),
    score_threshold: int = Query(default=84, ge=0, le=100),
    score_profile: str = Query(default="balanced", max_length=40),
    take_profit_percent: float = Query(default=5.0, ge=0, le=100),
    stop_loss_percent: float = Query(default=0.0, ge=0, le=100),
    max_holding_days: int = Query(default=0, ge=0, le=10000),
    cost_rate: float = Query(default=0.01, ge=0, le=1),
    force_liquidate_end: bool = Query(default=True),
    refresh: bool = Query(default=False),
):
    try:
        return serialize_qlib_backtest_result(
            backtest_qlib_one_lot_score_strategy(
                market=market,
                symbol=symbol,
                name=name,
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
                refresh=refresh,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Qlib 回测失败：{exc}") from exc


def _hk_pool_backtest_cache_key(
    *,
    limit: int | None,
    start_date: str,
    end_date: str | None,
    score_threshold: int,
    score_profile: str,
    take_profit_percent: float,
    stop_loss_percent: float,
    max_holding_days: int,
    cost_rate: float,
    force_liquidate_end: bool,
) -> dict:
    return {
        "start_date": start_date,
        "end_date": end_date or dt.date.today().isoformat(),
        "score_threshold": int(score_threshold),
        "score_profile": score_profile,
        "take_profit_percent": float(take_profit_percent),
        "stop_loss_percent": float(stop_loss_percent),
        "max_holding_days": max(0, int(max_holding_days)),
        "cost_rate": float(cost_rate),
        "force_liquidate_end": bool(force_liquidate_end),
        "limit": limit,
    }


def _hk_pool_backtest_cache_paths(cache_key: dict):
    base_path = get_settings().data_dir / "stock" / "qlib"
    return (
        _hk_pool_backtest_cache_path(cache_key),
        base_path / "hk_pool_backtest_one_lot_score_progress.json",
    )


def _start_hk_pool_backtest_job(
    *,
    job_key: str,
    cache_key: dict,
    progress_path: Path,
    refresh: bool,
    limit: int | None,
    start_date: str,
    end_date: str | None,
    score_threshold: int,
    score_profile: str,
    take_profit_percent: float,
    stop_loss_percent: float,
    max_holding_days: int,
    cost_rate: float,
    force_liquidate_end: bool,
) -> None:
    with _hk_pool_backtest_lock:
        existing = _hk_pool_backtest_jobs.get(job_key)
        if existing is not None and not existing.done():
            return

        def write_progress(result):
            _write_hk_pool_backtest_cache(progress_path, cache_key, result)

        def run_job():
            return backtest_hk_pool_one_lot_score(
                refresh=refresh,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                score_threshold=score_threshold,
                score_profile=score_profile,
                take_profit_percent=take_profit_percent,
                stop_loss_percent=stop_loss_percent,
                max_holding_days=max_holding_days,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
                progress_callback=write_progress,
            )

        _hk_pool_backtest_jobs[job_key] = _hk_pool_backtest_executor.submit(run_job)


def _parse_int_list(value: str, *, default: tuple[int, ...]) -> tuple[int, ...]:
    items: list[int] = []
    for part in value.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    return tuple(items) or default


def _parse_float_list(value: str, *, default: tuple[float, ...]) -> tuple[float, ...]:
    items: list[float] = []
    for part in value.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        items.append(float(part))
    return tuple(items) or default


def _strategy_search_cache_paths():
    base_path = get_settings().data_dir / "stock" / "qlib"
    return (
        base_path / "hk_pool_strategy_search.json",
        base_path / "hk_pool_strategy_search_progress.json",
    )


def _rotation_strategy_search_cache_key(
    *,
    years: tuple[int, ...],
    limit: int | None,
    candidates,
    min_annual_return_percent: float,
    require_beat_benchmark: bool,
) -> dict:
    return {
        "years": list(years),
        "limit": limit,
        "min_annual_return_percent": float(min_annual_return_percent),
        "require_beat_benchmark": bool(require_beat_benchmark),
        "candidates": [
            {
                "key": candidate.key,
                "score_profile": candidate.score_profile,
                "rank_metric": candidate.rank_metric,
                "market_filter": candidate.market_filter,
                "score_threshold": int(candidate.score_threshold),
                "min_amount": float(candidate.min_amount),
                "top_n": int(candidate.top_n),
                "rebalance": candidate.rebalance,
                "cost_rate": float(candidate.cost_rate),
            }
            for candidate in candidates
        ],
    }


def _rotation_strategy_search_cache_paths():
    base_path = get_settings().data_dir / "stock" / "qlib"
    return (
        base_path / "hk_pool_rotation_strategy_search.json",
        base_path / "hk_pool_rotation_strategy_search_progress.json",
    )


def _hk_connect_momentum_review_cache_key(
    *,
    end_date: str | None,
    capital: float,
    max_position_percent: float,
    universe_limit: int,
    min_market_cap: float,
    min_amount: float,
    top_n: int,
    lookback_days: int,
    volume_window_days: int,
    hold_days: int,
    cost_rate: float,
) -> dict:
    return {
        "end_date": end_date or dt.date.today().isoformat(),
        "capital": float(capital),
        "max_position_percent": float(max_position_percent),
        "universe_limit": int(universe_limit),
        "min_market_cap": float(min_market_cap),
        "min_amount": float(min_amount),
        "top_n": int(top_n),
        "lookback_days": int(lookback_days),
        "volume_window_days": int(volume_window_days),
        "hold_days": int(hold_days),
        "cost_rate": float(cost_rate),
    }


def _hk_connect_momentum_review_cache_paths():
    base_path = get_settings().data_dir / "stock" / "qlib"
    return (
        base_path / "hk_connect_momentum_review.json",
        base_path / "hk_connect_momentum_review_progress.json",
    )


def _etf_canary_rotation_cache_key(
    *,
    start_date: str,
    hold_days: int,
    top_n: int,
    cost: float,
    canary_threshold: float,
) -> dict:
    return {
        "start_date": start_date,
        "hold_days": int(hold_days),
        "top_n": int(top_n),
        "cost": float(cost),
        "canary_threshold": float(canary_threshold),
    }


def _etf_canary_rotation_cache_paths():
    base_path = get_settings().data_dir / "stock" / "qlib"
    return (
        base_path / "cross_asset_etf_canary_rotation.json",
        base_path / "cross_asset_etf_canary_rotation_progress.json",
    )


def _read_rotation_strategy_search_snapshot(path: Path, cache_key: dict) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
        return None
    payload.pop("cache_key", None)
    return payload


def _write_rotation_strategy_search_snapshot(path: Path, cache_key: dict, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["cache_key"] = cache_key
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _read_dict_snapshot(path: Path, cache_key: dict) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("cache_key") != cache_key:
        return None
    payload.pop("cache_key", None)
    return payload


def _write_dict_snapshot(path: Path, cache_key: dict, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["cache_key"] = cache_key
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _start_strategy_search_job(
    *,
    job_key: str,
    cache_key: dict,
    progress_path: Path,
    years: tuple[int, ...],
    limit: int | None,
    candidates,
    min_annual_return_percent: float,
    require_beat_benchmark: bool,
) -> None:
    with _hk_pool_strategy_lock:
        existing = _hk_pool_strategy_jobs.get(job_key)
        if existing is not None and not existing.done():
            return

        def write_progress(result):
            _write_strategy_search_cache(progress_path, cache_key, result)

        def run_job():
            try:
                return search_hk_pool_one_lot_score_strategies(
                    years=years,
                    limit=limit,
                    candidates=candidates,
                    refresh=True,
                    min_annual_return_percent=min_annual_return_percent,
                    require_beat_benchmark=require_beat_benchmark,
                    progress_callback=write_progress,
                )
            except Exception as exc:
                from backend.core.stock.qlib_screening import QlibStrategySearchResult

                error_result = QlibStrategySearchResult(
                    pool="hk_pool",
                    source="error:hk_pool_strategy_search",
                    years=years,
                    limit=limit,
                    benchmark_name="恒生指数",
                    items=(),
                    done_count=0,
                    candidate_count=len(candidates),
                    min_annual_return_percent=min_annual_return_percent,
                    require_beat_benchmark=require_beat_benchmark,
                    status="error",
                    error=str(exc),
                )
                write_progress(error_result)
                raise

        _hk_pool_strategy_jobs[job_key] = _hk_pool_strategy_executor.submit(run_job)


def _start_rotation_strategy_search_job(
    *,
    job_key: str,
    cache_key: dict,
    cache_path: Path,
    progress_path: Path,
    years: tuple[int, ...],
    limit: int | None,
    candidates,
    min_annual_return_percent: float,
    require_beat_benchmark: bool,
) -> None:
    with _hk_pool_strategy_lock:
        existing = _hk_pool_strategy_jobs.get(job_key)
        if existing is not None and not existing.done():
            return

        def write_progress(result):
            _write_rotation_strategy_search_snapshot(
                progress_path,
                cache_key,
                serialize_qlib_rotation_strategy_search_result(result),
            )

        def run_job():
            try:
                result = search_hk_pool_ranked_rotation_strategies(
                    years=years,
                    limit=limit,
                    candidates=candidates,
                    min_annual_return_percent=min_annual_return_percent,
                    require_beat_benchmark=require_beat_benchmark,
                    progress_callback=write_progress,
                )
                payload = serialize_qlib_rotation_strategy_search_result(result)
                _write_rotation_strategy_search_snapshot(cache_path, cache_key, payload)
                _write_rotation_strategy_search_snapshot(progress_path, cache_key, payload)
                return result
            except Exception as exc:
                payload = {
                    "pool": "hk_pool",
                    "source": "error:hk_pool_rotation_strategy_search",
                    "years": list(years),
                    "limit": limit,
                    "benchmark_name": "恒生指数",
                    "min_annual_return_percent": min_annual_return_percent,
                    "require_beat_benchmark": require_beat_benchmark,
                    "qualified_count": 0,
                    "done_count": 0,
                    "candidate_count": len(candidates),
                    "status": "error",
                    "error": str(exc),
                    "items": [],
                }
                _write_rotation_strategy_search_snapshot(progress_path, cache_key, payload)
                raise

        _hk_pool_strategy_jobs[job_key] = _hk_pool_strategy_executor.submit(run_job)


def _start_hk_connect_momentum_review_job(
    *,
    job_key: str,
    cache_key: dict,
    cache_path: Path,
    progress_path: Path,
    end_date: str | None,
    capital: float,
    max_position_percent: float,
    universe_limit: int,
    min_market_cap: float,
    min_amount: float,
    top_n: int,
    lookback_days: int,
    volume_window_days: int,
    hold_days: int,
    cost_rate: float,
) -> None:
    with _hk_pool_strategy_lock:
        existing = _hk_pool_strategy_jobs.get(job_key)
        if existing is not None and not existing.done():
            return

        def write_progress(result):
            _write_dict_snapshot(
                progress_path,
                cache_key,
                serialize_hk_connect_momentum_review_result(result),
            )

        def run_job():
            try:
                result = compute_hk_connect_momentum_review(
                    refresh=True,
                    end_date=end_date,
                    capital=capital,
                    max_position_percent=max_position_percent,
                    universe_limit=universe_limit,
                    min_market_cap=min_market_cap,
                    min_amount=min_amount,
                    top_n=top_n,
                    lookback_days=lookback_days,
                    volume_window_days=volume_window_days,
                    hold_days=hold_days,
                    cost_rate=cost_rate,
                    progress_callback=write_progress,
                )
                payload = serialize_hk_connect_momentum_review_result(result)
                _write_dict_snapshot(cache_path, cache_key, payload)
                _write_dict_snapshot(progress_path, cache_key, payload)
                return result
            except Exception as exc:
                payload = {
                    "strategy_key": "hk_connect_hsi60_largecap_volmom_top2",
                    "strategy_name": "港股通恒生60日线大市值成交额动量",
                    "source": "error:hk_connect_momentum_review",
                    "status": "error",
                    "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                    "signal_date": "",
                    "hsi_date": "",
                    "hsi_close": None,
                    "hsi_ma60": None,
                    "hsi_filter_passed": False,
                    "action": "wait",
                    "summary": f"复盘计算失败：{exc}",
                    "pool_count": 0,
                    "usable_count": 0,
                    "capital": capital,
                    "max_position_percent": max_position_percent,
                    "single_position_budget": capital * max_position_percent / max(1, top_n),
                    "cost_rate": cost_rate,
                    "universe_limit": universe_limit,
                    "min_market_cap": min_market_cap,
                    "min_amount": min_amount,
                    "top_n": top_n,
                    "lookback_days": lookback_days,
                    "volume_window_days": volume_window_days,
                    "hold_days": hold_days,
                    "error": str(exc),
                    "candidates": [],
                    "selected": [],
                }
                _write_dict_snapshot(progress_path, cache_key, payload)
                raise

        _hk_pool_strategy_jobs[job_key] = _hk_pool_strategy_executor.submit(run_job)


def run_hk_connect_momentum_review_snapshot_job() -> dict:
    cache_key = _hk_connect_momentum_review_cache_key(
        end_date=None,
        capital=100000.0,
        max_position_percent=0.7,
        universe_limit=300,
        min_market_cap=50_000_000_000.0,
        min_amount=500_000_000.0,
        top_n=2,
        lookback_days=10,
        volume_window_days=20,
        hold_days=20,
        cost_rate=0.0025,
    )
    cache_path, progress_path = _hk_connect_momentum_review_cache_paths()
    result = compute_hk_connect_momentum_review(
        refresh=True,
        end_date=None,
        capital=100000.0,
        max_position_percent=0.7,
        universe_limit=300,
        min_market_cap=50_000_000_000.0,
        min_amount=500_000_000.0,
        top_n=2,
        lookback_days=10,
        volume_window_days=20,
        hold_days=20,
        cost_rate=0.0025,
    )
    payload = serialize_hk_connect_momentum_review_result(result)
    _write_dict_snapshot(cache_path, cache_key, payload)
    _write_dict_snapshot(progress_path, cache_key, payload)
    return payload


def run_cross_asset_etf_canary_rotation_snapshot_job() -> dict:
    cache_key = _etf_canary_rotation_cache_key(
        start_date="2021-01-01",
        hold_days=10,
        top_n=1,
        cost=0.001,
        canary_threshold=0.5,
    )
    cache_path, progress_path = _etf_canary_rotation_cache_paths()
    payload = serialize_etf_rotation_backtest_result(
        compute_cross_asset_etf_canary_rotation(
            start_date="2021-01-01",
            hold_days=10,
            top_n=1,
            cost=0.001,
            canary_threshold=0.5,
        )
    )
    _write_dict_snapshot(cache_path, cache_key, payload)
    _write_dict_snapshot(progress_path, cache_key, payload)
    return payload


def run_market_intraday_persist_snapshot_job(
    *,
    include_market_kline: bool = True,
    limit: int | None = 300,
    day_count: int = 5,
) -> dict:
    targets = _collect_market_intraday_persist_targets(
        include_market_kline=include_market_kline,
        limit=limit,
    )
    target_trade_date = dt.date.today().isoformat()
    items: list[dict] = []
    for target in targets:
        market_code, normalized_symbol = resolve_akshare_market_symbol(
            format_market_symbol(target["market"], target["symbol"])
        )
        latest_trade_date = read_latest_persisted_history_date(
            market=market_code,
            symbol=normalized_symbol,
        )
        item = {
            "market": market_code,
            "symbol": normalized_symbol,
            "name": target["name"],
            "latest_daily_date": latest_trade_date,
            "target_trade_date": target_trade_date,
            "status": "skipped",
            "rows": 0,
            "trade_dates": [],
            "error": "",
        }
        try:
            intraday = fetch_akshare_etf_intraday(
                market=market_code,
                symbol=normalized_symbol,
                name=target["name"],
                trade_date=target_trade_date,
                period="1",
                day_count=day_count,
            )
            if intraday.rows:
                persist_akshare_intraday(intraday)
                trade_dates = sorted({row.time[:10] for row in intraday.rows if row.time})
                item["status"] = "persisted"
                item["rows"] = len(intraday.rows)
                item["trade_dates"] = trade_dates
            else:
                item["status"] = "empty"
                item["error"] = f"数据源未返回 {target_trade_date} 附近的分时数据"
        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)
        items.append(item)
    return {
        "provider": "akshare",
        "period": "1",
        "day_count": max(1, min(int(day_count or 1), 5)),
        "target_trade_date": target_trade_date,
        "target_count": len(targets),
        "items": items,
        "persisted": sum(1 for item in items if item["status"] == "persisted"),
        "failed": sum(1 for item in items if item["status"] == "error"),
    }


def _collect_market_intraday_persist_targets(
    *,
    include_market_kline: bool,
    limit: int | None,
) -> list[dict]:
    base_targets = [
        {"market": "SZ", "symbol": "159278", "name": "机器人PH"},
        {"market": "HK", "symbol": "03896", "name": "金山云"},
        {"market": "HK", "symbol": "01810", "name": "小米集团"},
    ]
    targets_by_key: dict[tuple[str, str], dict] = {}
    for target in base_targets:
        market, symbol = resolve_akshare_market_symbol(format_market_symbol(target["market"], target["symbol"]))
        targets_by_key[(market, symbol)] = {"market": market, "symbol": symbol, "name": target["name"], "priority": 0}

    if include_market_kline:
        with connect_market_data_db() as conn:
            rows = conn.execute(
                """
                SELECT
                    k.market,
                    k.symbol,
                    COALESCE(MAX(k.name), '') AS name,
                    MAX(k.time_key) AS latest_daily_date,
                    MAX(i.trade_date) AS latest_intraday_date,
                    COUNT(*) AS daily_rows
                FROM market_kline AS k
                LEFT JOIN market_intraday AS i
                  ON i.provider = k.provider
                 AND i.market = k.market
                 AND i.symbol = k.symbol
                 AND i.period = '1'
                WHERE k.provider = ?
                  AND k.ktype = 'daily'
                  AND k.time_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
                GROUP BY k.market, k.symbol
                """,
                (MARKET_DATA_PROVIDER_AKSHARE,),
            ).fetchall()
        for row in rows:
            market = str(row["market"] or "")
            symbol = str(row["symbol"] or "")
            if not market or not symbol:
                continue
            latest_daily_date = str(row["latest_daily_date"] or "")[:10]
            latest_intraday_date = str(row["latest_intraday_date"] or "")[:10]
            has_recent_intraday = bool(latest_intraday_date and latest_intraday_date >= latest_daily_date)
            is_cn_fund = market in {"SH", "SZ"} and symbol.startswith(("15", "16", "50", "51", "52", "56", "58"))
            priority = 1 if is_cn_fund else 2
            if latest_intraday_date:
                priority += 2
            if has_recent_intraday:
                priority += 3
            key = (market, symbol)
            targets_by_key.setdefault(
                key,
                {
                    "market": market,
                    "symbol": symbol,
                    "name": str(row["name"] or ""),
                    "priority": priority,
                    "latest_daily_date": latest_daily_date,
                    "latest_intraday_date": latest_intraday_date,
                    "daily_rows": int(row["daily_rows"] or 0),
                },
            )

    targets = sorted(
        targets_by_key.values(),
        key=lambda item: (
            int(item.get("priority") or 99),
            str(item.get("latest_intraday_date") or ""),
            -int(item.get("daily_rows") or 0),
            str(item.get("market") or ""),
            str(item.get("symbol") or ""),
        ),
    )
    if limit is not None and limit > 0:
        targets = targets[:limit]
    return targets


@router.get("/qlib/hk-connect-momentum-review")
def get_eastmoney_qlib_hk_connect_momentum_review(
    refresh: bool = Query(default=False),
    background: bool = Query(default=False),
    progress: bool = Query(default=False),
    end_date: str | None = Query(default=None),
    capital: float = Query(default=100000.0, ge=1000, le=100000000),
    max_position_percent: float = Query(default=0.7, ge=0.01, le=1),
    universe_limit: int = Query(default=300, ge=20, le=1000),
    min_market_cap: float = Query(default=50_000_000_000.0, ge=0),
    min_amount: float = Query(default=500_000_000.0, ge=0),
    top_n: int = Query(default=2, ge=1, le=10),
    lookback_days: int = Query(default=10, ge=1, le=240),
    volume_window_days: int = Query(default=20, ge=1, le=240),
    hold_days: int = Query(default=20, ge=1, le=240),
    cost_rate: float = Query(default=0.0025, ge=0, le=0.1),
):
    try:
        cache_key = _hk_connect_momentum_review_cache_key(
            end_date=end_date,
            capital=capital,
            max_position_percent=max_position_percent,
            universe_limit=universe_limit,
            min_market_cap=min_market_cap,
            min_amount=min_amount,
            top_n=top_n,
            lookback_days=lookback_days,
            volume_window_days=volume_window_days,
            hold_days=hold_days,
            cost_rate=cost_rate,
        )
        cache_path, progress_path = _hk_connect_momentum_review_cache_paths()
        job_key = f"hk-connect-momentum:{cache_key}"
        if progress:
            payload = _read_dict_snapshot(progress_path, cache_key) or _read_dict_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
            return {
                "strategy_key": "hk_connect_hsi60_largecap_volmom_top2",
                "strategy_name": "港股通恒生60日线大市值成交额动量",
                "source": "running:0/0",
                "status": "running",
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "signal_date": "",
                "hsi_date": "",
                "hsi_close": None,
                "hsi_ma60": None,
                "hsi_filter_passed": False,
                "action": "wait",
                "summary": "等待后台复盘结果",
                "pool_count": 0,
                "usable_count": 0,
                "capital": capital,
                "max_position_percent": max_position_percent,
                "single_position_budget": capital * max_position_percent / max(1, top_n),
                "cost_rate": cost_rate,
                "universe_limit": universe_limit,
                "min_market_cap": min_market_cap,
                "min_amount": min_amount,
                "top_n": top_n,
                "lookback_days": lookback_days,
                "volume_window_days": volume_window_days,
                "hold_days": hold_days,
                "error": "",
                "candidates": [],
                "selected": [],
            }
        if background and refresh:
            _start_hk_connect_momentum_review_job(
                job_key=job_key,
                cache_key=cache_key,
                cache_path=cache_path,
                progress_path=progress_path,
                end_date=end_date,
                capital=capital,
                max_position_percent=max_position_percent,
                universe_limit=universe_limit,
                min_market_cap=min_market_cap,
                min_amount=min_amount,
                top_n=top_n,
                lookback_days=lookback_days,
                volume_window_days=volume_window_days,
                hold_days=hold_days,
                cost_rate=cost_rate,
            )
            payload = _read_dict_snapshot(progress_path, cache_key) or _read_dict_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
            return {
                "strategy_key": "hk_connect_hsi60_largecap_volmom_top2",
                "strategy_name": "港股通恒生60日线大市值成交额动量",
                "source": "running:0/0",
                "status": "running",
                "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
                "signal_date": "",
                "hsi_date": "",
                "hsi_close": None,
                "hsi_ma60": None,
                "hsi_filter_passed": False,
                "action": "wait",
                "summary": "后台复盘已启动",
                "pool_count": 0,
                "usable_count": 0,
                "capital": capital,
                "max_position_percent": max_position_percent,
                "single_position_budget": capital * max_position_percent / max(1, top_n),
                "cost_rate": cost_rate,
                "universe_limit": universe_limit,
                "min_market_cap": min_market_cap,
                "min_amount": min_amount,
                "top_n": top_n,
                "lookback_days": lookback_days,
                "volume_window_days": volume_window_days,
                "hold_days": hold_days,
                "error": "",
                "candidates": [],
                "selected": [],
            }
        if not refresh:
            payload = _read_dict_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
        result = compute_hk_connect_momentum_review(
            refresh=refresh,
            end_date=end_date,
            capital=capital,
            max_position_percent=max_position_percent,
            universe_limit=universe_limit,
            min_market_cap=min_market_cap,
            min_amount=min_amount,
            top_n=top_n,
            lookback_days=lookback_days,
            volume_window_days=volume_window_days,
            hold_days=hold_days,
            cost_rate=cost_rate,
        )
        payload = serialize_hk_connect_momentum_review_result(result)
        _write_dict_snapshot(cache_path, cache_key, payload)
        return payload
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取港股通策略复盘失败：{exc}") from exc


@router.get("/qlib/backtest/hk-pool-one-lot-score")
def get_eastmoney_qlib_hk_pool_one_lot_score_backtest(
    refresh: bool = Query(default=False),
    background: bool = Query(default=False),
    progress: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=5000),
    detail_limit: int = Query(default=300, ge=1, le=5000),
    start_date: str = Query(default="2025-01-01"),
    end_date: str | None = Query(default=None),
    score_threshold: int = Query(default=84, ge=0, le=100),
    score_profile: str = Query(default="balanced", max_length=40),
    take_profit_percent: float = Query(default=5.0, ge=0, le=100),
    stop_loss_percent: float = Query(default=0.0, ge=0, le=100),
    max_holding_days: int = Query(default=0, ge=0, le=10000),
    cost_rate: float = Query(default=0.01, ge=0, le=1),
    force_liquidate_end: bool = Query(default=True),
):
    try:
        cache_key = _hk_pool_backtest_cache_key(
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            score_threshold=score_threshold,
            score_profile=score_profile,
            take_profit_percent=take_profit_percent,
            stop_loss_percent=stop_loss_percent,
            max_holding_days=max_holding_days,
            cost_rate=cost_rate,
            force_liquidate_end=force_liquidate_end,
        )
        cache_path, progress_path = _hk_pool_backtest_cache_paths(cache_key)
        job_key = str(cache_key)
        if progress:
            result = _read_hk_pool_backtest_cache(progress_path, cache_key) or _read_hk_pool_backtest_cache(cache_path, cache_key)
            if result is not None:
                return serialize_qlib_pool_backtest_result(result, detail_limit=detail_limit)
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "target_count": 0,
                "tested_count": 0,
                "skipped_count": 0,
                "start_date": start_date,
                "end_date": end_date or dt.date.today().isoformat(),
                "score_threshold": score_threshold,
                "score_profile": score_profile,
                "take_profit_percent": take_profit_percent,
                "stop_loss_percent": stop_loss_percent,
                "max_holding_days": max_holding_days,
                "cost_rate": cost_rate,
                "total_profit": 0,
                "total_invested": 0,
                "total_fee": 0,
                "max_capital_used": 0,
                "trade_count": 0,
                "closed_trade_count": 0,
                "open_position_count": 0,
                "force_liquidate_end": force_liquidate_end,
                "benchmarks": [],
                "error": "",
                "items": [],
            }
        if background and refresh:
            _start_hk_pool_backtest_job(
                job_key=job_key,
                cache_key=cache_key,
                progress_path=progress_path,
                refresh=refresh,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                score_threshold=score_threshold,
                score_profile=score_profile,
                take_profit_percent=take_profit_percent,
                stop_loss_percent=stop_loss_percent,
                max_holding_days=max_holding_days,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
            )
            result = _read_hk_pool_backtest_cache(progress_path, cache_key) or _read_hk_pool_backtest_cache(cache_path, cache_key)
            if result is not None:
                return serialize_qlib_pool_backtest_result(result, detail_limit=detail_limit)
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "target_count": 0,
                "tested_count": 0,
                "skipped_count": 0,
                "start_date": start_date,
                "end_date": end_date or dt.date.today().isoformat(),
                "score_threshold": score_threshold,
                "score_profile": score_profile,
                "take_profit_percent": take_profit_percent,
                "stop_loss_percent": stop_loss_percent,
                "max_holding_days": max_holding_days,
                "cost_rate": cost_rate,
                "total_profit": 0,
                "total_invested": 0,
                "total_fee": 0,
                "max_capital_used": 0,
                "trade_count": 0,
                "closed_trade_count": 0,
                "open_position_count": 0,
                "force_liquidate_end": force_liquidate_end,
                "benchmarks": [],
                "error": "",
                "items": [],
            }
        return serialize_qlib_pool_backtest_result(
            backtest_hk_pool_one_lot_score(
                refresh=refresh,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                score_threshold=score_threshold,
                score_profile=score_profile,
                take_profit_percent=take_profit_percent,
                stop_loss_percent=stop_loss_percent,
                max_holding_days=max_holding_days,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
            ),
            detail_limit=detail_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取港股池 Qlib 回测失败：{exc}") from exc


@router.get("/qlib/backtest/hk-pool-strategy-search")
def get_eastmoney_qlib_hk_pool_strategy_search(
    years: str = Query(default="2023,2024,2025", max_length=80),
    limit: int | None = Query(default=300, ge=1, le=5000),
    score_thresholds: str = Query(default="70,76,80,84,88,90", max_length=120),
    take_profit_percents: str = Query(default="5,8,10,15", max_length=120),
    stop_loss_percents: str = Query(default="0,8", max_length=120),
    max_holding_days: str = Query(default="0,60", max_length=120),
    score_profiles: str = Query(default="balanced,trend_momentum,short_reversal,low_volatility,volume_breakout", max_length=200),
    cost_rate: float = Query(default=0.01, ge=0, le=0.2),
    min_annual_return_percent: float = Query(default=5.0, ge=-100, le=1000),
    require_beat_benchmark: bool = Query(default=True),
    background: bool = Query(default=False),
    progress: bool = Query(default=False),
):
    try:
        parsed_years = tuple(sorted({year for year in _parse_int_list(years, default=(2023, 2024, 2025)) if year >= 1990}))
        candidates = build_qlib_strategy_candidates(
            score_thresholds=_parse_int_list(score_thresholds, default=(70, 76, 80, 84, 88, 90)),
            take_profit_percents=_parse_float_list(take_profit_percents, default=(5.0, 8.0, 10.0, 15.0)),
            stop_loss_percents=_parse_float_list(stop_loss_percents, default=(0.0, 8.0)),
            max_holding_days_values=_parse_int_list(max_holding_days, default=(0, 60)),
            score_profiles=tuple(part.strip() for part in score_profiles.replace("，", ",").split(",") if part.strip()),
            cost_rate=cost_rate,
        )
        cache_key = _strategy_search_cache_key(
            years=parsed_years,
            limit=limit,
            candidates=candidates,
            force_liquidate_end=True,
            min_annual_return_percent=min_annual_return_percent,
            require_beat_benchmark=require_beat_benchmark,
        )
        cache_path, progress_path = _strategy_search_cache_paths()
        if progress:
            result = _read_strategy_search_cache(progress_path, cache_key) or _read_strategy_search_cache(cache_path, cache_key)
            if result is not None:
                return serialize_qlib_strategy_search_result(result)
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "years": list(parsed_years),
                "limit": limit,
                "benchmark_name": "恒生指数",
                "min_annual_return_percent": min_annual_return_percent,
                "require_beat_benchmark": require_beat_benchmark,
                "qualified_count": 0,
                "done_count": 0,
                "candidate_count": len(candidates),
                "status": "running",
                "error": "",
                "items": [],
            }
        if background:
            _start_strategy_search_job(
                job_key=str(cache_key),
                cache_key=cache_key,
                progress_path=progress_path,
                years=parsed_years,
                limit=limit,
                candidates=candidates,
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            )
            result = _read_strategy_search_cache(progress_path, cache_key) or _read_strategy_search_cache(cache_path, cache_key)
            if result is not None:
                return serialize_qlib_strategy_search_result(result)
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "years": list(parsed_years),
                "limit": limit,
                "benchmark_name": "恒生指数",
                "min_annual_return_percent": min_annual_return_percent,
                "require_beat_benchmark": require_beat_benchmark,
                "qualified_count": 0,
                "done_count": 0,
                "candidate_count": len(candidates),
                "status": "running",
                "error": "",
                "items": [],
            }
        return serialize_qlib_strategy_search_result(
            search_hk_pool_one_lot_score_strategies(
                years=parsed_years,
                limit=limit,
                candidates=candidates,
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"搜索港股池策略失败：{exc}") from exc


@router.get("/qlib/backtest/hk-pool-rotation-strategy-search")
def get_eastmoney_qlib_hk_pool_rotation_strategy_search(
    years: str = Query(default="2023,2024,2025", max_length=80),
    limit: int | None = Query(default=300, ge=1, le=5000),
    score_profiles: str = Query(default="balanced", max_length=200),
    rank_metrics: str = Query(default="score,volume_breakout_rank,value_score_rank", max_length=200),
    market_filters: str = Query(default="none,hsi_ma60", max_length=120),
    score_thresholds: str = Query(default="0,70,76", max_length=120),
    min_amounts: str = Query(default="10000000", max_length=160),
    top_n_values: str = Query(default="3,5,10", max_length=120),
    rebalances: str = Query(default="monthly,quarterly", max_length=120),
    cost_rate: float = Query(default=0.01, ge=0, le=0.2),
    min_annual_return_percent: float = Query(default=5.0, ge=-100, le=1000),
    require_beat_benchmark: bool = Query(default=True),
    background: bool = Query(default=False),
    progress: bool = Query(default=False),
):
    try:
        parsed_years = tuple(sorted({year for year in _parse_int_list(years, default=(2023, 2024, 2025)) if year >= 1990}))
        candidates = build_qlib_rotation_strategy_candidates(
            score_profiles=tuple(part.strip() for part in score_profiles.replace("，", ",").split(",") if part.strip()),
            rank_metrics=tuple(part.strip() for part in rank_metrics.replace("，", ",").split(",") if part.strip()),
            market_filters=tuple(part.strip() for part in market_filters.replace("，", ",").split(",") if part.strip()),
            score_thresholds=_parse_int_list(score_thresholds, default=(0, 70, 76)),
            min_amounts=_parse_float_list(min_amounts, default=(10_000_000.0,)),
            top_n_values=_parse_int_list(top_n_values, default=(3, 5, 10)),
            rebalances=tuple(part.strip() for part in rebalances.replace("，", ",").split(",") if part.strip()),
            cost_rate=cost_rate,
        )
        cache_key = _rotation_strategy_search_cache_key(
            years=parsed_years,
            limit=limit,
            candidates=candidates,
            min_annual_return_percent=min_annual_return_percent,
            require_beat_benchmark=require_beat_benchmark,
        )
        cache_path, progress_path = _rotation_strategy_search_cache_paths()
        if progress:
            payload = _read_rotation_strategy_search_snapshot(progress_path, cache_key) or _read_rotation_strategy_search_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "years": list(parsed_years),
                "limit": limit,
                "benchmark_name": "恒生指数",
                "min_annual_return_percent": min_annual_return_percent,
                "require_beat_benchmark": require_beat_benchmark,
                "qualified_count": 0,
                "done_count": 0,
                "candidate_count": len(candidates),
                "status": "running",
                "error": "",
                "items": [],
            }
        if background:
            _start_rotation_strategy_search_job(
                job_key=f"rotation:{cache_key}",
                cache_key=cache_key,
                cache_path=cache_path,
                progress_path=progress_path,
                years=parsed_years,
                limit=limit,
                candidates=candidates,
                min_annual_return_percent=min_annual_return_percent,
                require_beat_benchmark=require_beat_benchmark,
            )
            payload = _read_rotation_strategy_search_snapshot(progress_path, cache_key) or _read_rotation_strategy_search_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
            return {
                "pool": "hk_pool",
                "source": "running:0/0",
                "years": list(parsed_years),
                "limit": limit,
                "benchmark_name": "恒生指数",
                "min_annual_return_percent": min_annual_return_percent,
                "require_beat_benchmark": require_beat_benchmark,
                "qualified_count": 0,
                "done_count": 0,
                "candidate_count": len(candidates),
                "status": "running",
                "error": "",
                "items": [],
            }
        result = search_hk_pool_ranked_rotation_strategies(
            years=parsed_years,
            limit=limit,
            candidates=candidates,
            min_annual_return_percent=min_annual_return_percent,
            require_beat_benchmark=require_beat_benchmark,
        )
        payload = serialize_qlib_rotation_strategy_search_result(result)
        _write_rotation_strategy_search_snapshot(cache_path, cache_key, payload)
        return payload
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"搜索港股池轮动策略失败：{exc}") from exc


@router.get("/qlib/backtest/cross-asset-etf-canary-rotation")
def get_eastmoney_cross_asset_etf_canary_rotation_backtest(
    refresh: bool = Query(default=False),
    progress: bool = Query(default=False),
    start_date: str = Query(default="2021-01-01", max_length=10),
    hold_days: int = Query(default=10, ge=1, le=60),
    top_n: int = Query(default=1, ge=1, le=3),
    cost: float = Query(default=0.001, ge=0, le=0.02),
    canary_threshold: float = Query(default=0.5, ge=-10, le=10),
):
    try:
        cache_key = _etf_canary_rotation_cache_key(
            start_date=start_date,
            hold_days=hold_days,
            top_n=top_n,
            cost=cost,
            canary_threshold=canary_threshold,
        )
        cache_path, progress_path = _etf_canary_rotation_cache_paths()
        if progress:
            payload = _read_dict_snapshot(progress_path, cache_key) or _read_dict_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
        if not refresh:
            payload = _read_dict_snapshot(cache_path, cache_key)
            if payload is not None:
                return payload
        payload = serialize_etf_rotation_backtest_result(
            compute_cross_asset_etf_canary_rotation(
                start_date=start_date,
                hold_days=hold_days,
                top_n=top_n,
                cost=cost,
                canary_threshold=canary_threshold,
            )
        )
        _write_dict_snapshot(cache_path, cache_key, payload)
        _write_dict_snapshot(progress_path, cache_key, payload)
        return payload
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"计算跨资产ETF轮动失败：{exc}") from exc


@router.get("/market-history/akshare")
def get_akshare_market_history(
    market: str = Query(default="SZ", max_length=8),
    symbol: str = Query(default="159278", min_length=1, max_length=12),
    name: str = Query(default="机器人PH", max_length=80),
    period: str = Query(default="daily"),
    start_date: str = Query(default="2025-08-12"),
    end_date: str | None = Query(default=None),
    adjust: str = Query(default=""),
    refresh: bool = Query(default=False),
):
    if not refresh and period in {"daily", "weekly", "monthly"}:
        cached = read_cached_akshare_history(
            symbol=symbol,
            market=market,
            name=name,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if cached is not None:
            return serialize_akshare_stock_history(cached)

    try:
        history = fetch_akshare_stock_history(
            market=market,
            symbol=symbol,
            name=name,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if not history.rows:
            qlib_cached = _read_qlib_source_history(
                market=market,
                symbol=symbol,
                name=name,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if qlib_cached is not None and qlib_cached.rows:
                return serialize_akshare_stock_history(qlib_cached)
        if history.period in {"daily", "weekly", "monthly"}:
            cache_akshare_history(history)
        return serialize_akshare_stock_history(history)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if period in {"daily", "weekly", "monthly"}:
            cached = read_cached_akshare_history(
                symbol=symbol,
                market=market,
                name=name,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if cached is not None:
                return serialize_akshare_stock_history(cached)
        qlib_cached = _read_qlib_source_history(
            market=market,
            symbol=symbol,
            name=name,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if qlib_cached is not None and qlib_cached.rows:
            return serialize_akshare_stock_history(qlib_cached)
        market_code, normalized_symbol = resolve_akshare_market_symbol(format_market_symbol(market, symbol))
        return serialize_akshare_stock_history(
            AkshareStockHistory(
                provider="akshare-error",
                market=market_code,
                symbol=normalized_symbol,
                name=name,
                period=normalize_ktype(period),
                adjust=adjust,
                start_date=akshare_date_to_iso(start_date),
                end_date=akshare_date_to_iso(end_date) if end_date else "",
                rows=(),
                error=str(exc),
            )
        )


@router.get("/market-intraday/akshare")
def get_akshare_market_intraday(
    market: str = Query(default="SZ", max_length=8),
    symbol: str = Query(default="159278", min_length=1, max_length=12),
    name: str = Query(default="机器人PH", max_length=80),
    trade_date: str | None = Query(default=None),
    period: str = Query(default="1"),
    day_count: int = Query(default=1, ge=1, le=5),
    refresh: bool = Query(default=False),
):
    market_code, normalized_symbol = resolve_akshare_market_symbol(format_market_symbol(market, symbol))
    latest_trade_date = read_latest_persisted_history_date(
        market=market_code,
        symbol=normalized_symbol,
    )
    requested_trade_date = akshare_date_to_iso(trade_date) if trade_date else ""
    target_trade_date = requested_trade_date or latest_trade_date

    def serialize_with_target(intraday: AkshareEtfIntraday) -> dict:
        payload = serialize_akshare_etf_intraday(intraday)
        payload["target_trade_date"] = target_trade_date or intraday.trade_date
        payload["display_trade_date"] = intraday.trade_date
        return payload

    if not refresh:
        cached = read_persisted_akshare_intraday(
            market=market,
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            period=period,
            day_count=day_count,
        )
        if cached is not None:
            if not requested_trade_date and latest_trade_date and cached.trade_date < latest_trade_date:
                return serialize_with_target(
                    AkshareEtfIntraday(
                        provider=cached.provider,
                        market=cached.market,
                        symbol=cached.symbol,
                        name=cached.name,
                        period=cached.period,
                        trade_date=cached.trade_date,
                        rows=cached.rows,
                        error=f"目标交易日 {latest_trade_date} 分时数据尚未持久化，当前显示最近已持久化交易日 {cached.trade_date}",
                    )
                )
            return serialize_with_target(cached)
        return serialize_with_target(
            AkshareEtfIntraday(
                provider="market-data",
                market=market_code,
                symbol=normalized_symbol,
                name=name,
                period=period,
                trade_date=target_trade_date,
                rows=(),
                error=(
                    f"本地暂无目标交易日 {target_trade_date} 分时持久化数据；可点补下载尝试拉取并落库"
                    if target_trade_date
                    else "本地暂无分时持久化数据；可点补下载尝试拉取并落库"
                ),
            )
        )

    try:
        intraday = fetch_akshare_etf_intraday(
            market=market,
            symbol=symbol,
            name=name,
            trade_date=requested_trade_date or latest_trade_date or None,
            period=period,
            day_count=day_count,
        )
        if intraday.rows:
            persist_akshare_intraday(intraday)
            return serialize_with_target(intraday)
        return serialize_with_target(
            AkshareEtfIntraday(
                provider="akshare-empty",
                market=intraday.market,
                symbol=intraday.symbol,
                name=intraday.name,
                period=intraday.period,
                trade_date=intraday.trade_date,
                rows=(),
                error=f"AKShare 未返回 {intraday.trade_date} 分时数据",
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        cached = read_persisted_akshare_intraday(
            market=market,
            symbol=symbol,
            name=name,
            trade_date=requested_trade_date or latest_trade_date or trade_date,
            period=period,
            day_count=day_count,
        )
        if cached is not None:
            return serialize_with_target(
                AkshareEtfIntraday(
                    provider=cached.provider,
                    market=cached.market,
                    symbol=cached.symbol,
                    name=cached.name,
                    period=cached.period,
                    trade_date=cached.trade_date,
                    rows=cached.rows,
                    error=str(exc),
                )
            )
        if not requested_trade_date:
            fallback_cached = read_persisted_akshare_intraday(
                market=market,
                symbol=symbol,
                name=name,
                trade_date=None,
                period=period,
                day_count=day_count,
            )
            if fallback_cached is not None:
                target_date = latest_trade_date or fallback_cached.trade_date
                return serialize_with_target(
                    AkshareEtfIntraday(
                        provider=fallback_cached.provider,
                        market=fallback_cached.market,
                        symbol=fallback_cached.symbol,
                        name=fallback_cached.name,
                        period=fallback_cached.period,
                        trade_date=fallback_cached.trade_date,
                        rows=fallback_cached.rows,
                        error=f"补下载目标交易日 {target_date} 分时失败，当前显示最近已持久化交易日 {fallback_cached.trade_date}：{exc}",
                    )
                )
        return serialize_with_target(
            AkshareEtfIntraday(
                provider="akshare-error",
                market=market_code,
                symbol=normalized_symbol,
                name=name,
                period=period,
                trade_date=requested_trade_date or latest_trade_date or "",
                rows=(),
                error=(
                    f"本地暂无目标交易日 {target_trade_date} 分时持久化数据；补下载失败：{exc}"
                    if target_trade_date
                    else str(exc)
                ),
            )
        )


def persist_akshare_intraday(intraday: AkshareEtfIntraday) -> None:
    market, symbol = resolve_akshare_market_symbol(format_market_symbol(intraday.market, intraday.symbol))
    fallback_trade_date = akshare_date_to_iso(intraday.trade_date)
    rows_by_trade_date: dict[str, list[dict]] = {}
    for row in intraday.rows:
        row_trade_date = akshare_date_to_iso(row.time[:10]) or fallback_trade_date
        if not row_trade_date:
            continue
        rows_by_trade_date.setdefault(row_trade_date, []).append(
            {
            "time_key": row.time,
            "trade_date": row_trade_date,
            "open": row.open,
            "close": row.close,
            "high": row.high,
            "low": row.low,
            "volume": row.volume,
            "turnover": row.amount,
            "average_price": row.average_price,
            "raw_symbol": row.symbol,
            }
        )
    with connect_market_data_db() as conn:
        for trade_date, rows in rows_by_trade_date.items():
            target = MarketHistoryTarget(
                market=market,
                symbol=symbol,
                provider_code=symbol,
                name=intraday.name,
                sources=("akshare:intraday",),
                first_trade_date=trade_date,
                start_date=trade_date,
                end_date=trade_date,
            )
            upsert_intraday_rows(
                conn,
                provider=MARKET_DATA_PROVIDER_AKSHARE,
                target=target,
                period=intraday.period,
                trade_date=trade_date,
                rows=rows,
            )


def read_persisted_akshare_intraday(
    *,
    market: str,
    symbol: str,
    name: str,
    trade_date: str | None,
    period: str,
    day_count: int,
) -> AkshareEtfIntraday | None:
    market_code, normalized_symbol = resolve_akshare_market_symbol(format_market_symbol(market, symbol))
    normalized_period = str(period or "1")
    normalized_day_count = max(1, min(int(day_count or 1), 5))
    requested_trade_date = akshare_date_to_iso(trade_date) if trade_date else ""

    with connect_market_data_db() as conn:
        if requested_trade_date:
            trade_dates = [requested_trade_date]
        else:
            date_rows = conn.execute(
                """
                SELECT DISTINCT trade_date
                FROM market_intraday
                WHERE provider = ?
                  AND market = ?
                  AND symbol = ?
                  AND period = ?
                ORDER BY trade_date DESC
                LIMIT ?
                """,
                (
                    MARKET_DATA_PROVIDER_AKSHARE,
                    market_code,
                    normalized_symbol,
                    normalized_period,
                    normalized_day_count,
                ),
            ).fetchall()
            trade_dates = [str(row["trade_date"] or "") for row in reversed(date_rows) if row["trade_date"]]
        if not trade_dates:
            return None

        rows = conn.execute(
            f"""
            SELECT *
            FROM market_intraday
            WHERE provider = ?
              AND market = ?
              AND symbol = ?
              AND period = ?
              AND trade_date IN ({",".join("?" for _ in trade_dates)})
            ORDER BY time_key
            """,
            (
                MARKET_DATA_PROVIDER_AKSHARE,
                market_code,
                normalized_symbol,
                normalized_period,
                *trade_dates,
            ),
        ).fetchall()

    if not rows:
        return None

    intraday_rows = [
        AkshareEtfIntradayRow(
            time=str(row["time_key"] or ""),
            symbol=normalized_symbol,
            open=row["open"],
            close=row["close"],
            high=row["high"],
            low=row["low"],
            volume=row["volume"],
            amount=row["turnover"],
            average_price=row["average_price"],
        )
        for row in rows
    ]
    return AkshareEtfIntraday(
        provider="market-data",
        market=market_code,
        symbol=normalized_symbol,
        name=name.strip() or str(rows[-1]["name"] or ""),
        period=normalized_period,
        trade_date=str(rows[-1]["trade_date"] or ""),
        rows=tuple(intraday_rows),
    )


def read_latest_persisted_history_date(*, market: str, symbol: str) -> str:
    with connect_market_data_db() as conn:
        row = conn.execute(
            """
            SELECT MAX(time_key) AS max_time_key
            FROM market_kline
            WHERE provider = ?
              AND market = ?
              AND symbol = ?
              AND ktype = 'daily'
              AND time_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
            """,
            (MARKET_DATA_PROVIDER_AKSHARE, market, symbol),
        ).fetchone()
    return str(row["max_time_key"] or "")[:10] if row else ""


def cache_akshare_history(history: AkshareStockHistory) -> None:
    market, symbol = resolve_akshare_market_symbol(format_market_symbol(history.market, history.symbol))
    target = MarketHistoryTarget(
        market=market,
        symbol=symbol,
        provider_code=symbol,
        name=history.name,
        sources=("akshare:history",),
        first_trade_date=history.start_date,
        start_date=akshare_date_to_iso(history.start_date),
        end_date=akshare_date_to_iso(history.end_date),
    )
    rows = [
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
        for row in history.rows
    ]
    with connect_market_data_db() as conn:
        upsert_kline_rows(
            conn,
            provider=MARKET_DATA_PROVIDER_AKSHARE,
            target=target,
            ktype=normalize_ktype(history.period),
            autype=normalize_autype(history.adjust),
            rows=rows,
            provisional_date=target.end_date,
        )


def read_cached_akshare_history(
    *,
    market: str,
    symbol: str,
    name: str,
    period: str,
    start_date: str,
    end_date: str | None,
    adjust: str,
) -> AkshareStockHistory | None:
    market, normalized_symbol = resolve_akshare_market_symbol(format_market_symbol(market, symbol))
    normalized_period = normalize_ktype(period)
    normalized_adjust = normalize_autype(adjust)
    start_iso = akshare_date_to_iso(start_date)
    end_iso = akshare_date_to_iso(end_date) if end_date else None
    query = """
        SELECT *
        FROM market_kline
        WHERE provider = ?
          AND market = ?
          AND symbol = ?
          AND ktype = ?
          AND autype = ?
          AND time_key >= ?
    """
    params: list[object] = [
        MARKET_DATA_PROVIDER_AKSHARE,
        market,
        normalized_symbol,
        normalized_period,
        normalized_adjust,
        start_iso,
    ]
    if end_iso:
        query += " AND time_key <= ?"
        params.append(end_iso)
    query += " ORDER BY time_key"

    with connect_market_data_db() as conn:
        rows = conn.execute(query, params).fetchall()
    if not rows:
        return None

    cached_history_rows: list[AkshareStockHistoryRow] = []
    for row in rows:
        open_price, close_price, high_price, low_price = _normalize_ohlc_prices(
            row["open"],
            row["close"],
            row["high"],
            row["low"],
        )
        cached_history_rows.append(
            AkshareStockHistoryRow(
                date=str(row["time_key"] or "")[:10],
                symbol=normalized_symbol,
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

    return AkshareStockHistory(
        provider="akshare-cache",
        market=market,
        symbol=normalized_symbol,
        name=name.strip() or str(rows[-1]["name"] or ""),
        period=normalized_period,
        adjust="" if normalized_adjust == "none" else normalized_adjust,
        start_date=start_iso.replace("-", ""),
        end_date=(end_iso or str(rows[-1]["time_key"])[:10]).replace("-", ""),
        rows=tuple(cached_history_rows),
    )


def resolve_akshare_market_symbol(symbol: str) -> tuple[str, str]:
    text = str(symbol or "").strip()
    market = ""
    if "." in text:
        market, text = text.split(".", 1)
    normalized = normalize_market_code(market, text)
    if normalized is None:
        raise ValueError("股票代码不能为空")
    return normalized


def format_market_symbol(market: str, symbol: str) -> str:
    text = str(symbol or "").strip()
    return text if "." in text else f"{market}.{text}"


def akshare_date_to_iso(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


@router.get("/trade-snapshot")
def get_trade_snapshot(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    try:
        return snapshot_to_dict(read_trade_snapshot(start_date=start_date, end_date=end_date))
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取东方财富交易页失败：{exc}") from exc


@router.post("/trade-account/open")
def open_eastmoney_trade_account_page():
    try:
        return open_trade_account_page()
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"打开东方财富交易页失败：{exc}") from exc


@router.post("/sync")
def sync_eastmoney_trade_data(
    payload: EastmoneySyncRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    try:
        run = sync_trade_data(
            session,
            user_id=int(current_user.id),
            start_date=payload.start_date,
            end_date=payload.end_date,
        )
        return {
            **run,
            "sheet_workbook": refresh_eastmoney_sheet_workbook(
                session,
                user_id=int(current_user.id),
                actor_user_id=int(current_user.id),
            ),
        }
    except EastmoneyTradeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"同步东方财富数据失败：{exc}") from exc


@router.post("/sheet-workbook/refresh")
def refresh_eastmoney_sheet_file(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return refresh_eastmoney_sheet_workbook(
        session,
        user_id=int(current_user.id),
        actor_user_id=int(current_user.id),
    )


@router.post("/trade-detail/import/ocr")
async def import_eastmoney_trade_detail_from_ocr(
    image: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    if image.content_type and not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="请粘贴图片截图")

    image_bytes = await image.read()
    await image.close()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="截图内容为空")

    suffix = Path(image.filename or "").suffix or ".png"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(image_bytes)
            temp_path = Path(temp_file.name)
        preview = run_paddle_ocr_preview(temp_path, shape_type="rectangle")
        row, lines = parse_mobile_trade_detail_from_ocr_document(preview.get("document") or {})
        result = import_mobile_trade_detail_record(
            session,
            user_id=int(current_user.id),
            row=row,
            ocr_lines=lines,
        )
        return {
            **result,
            "sheet_workbook": refresh_eastmoney_sheet_workbook(
                session,
                user_id=int(current_user.id),
                actor_user_id=int(current_user.id),
            ),
        }
    except OcrPreviewError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (EastmoneyTradeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"导入东方财富截图失败：{exc}") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@router.get("/trade-records")
def get_local_trade_records(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    source: str | None = Query(default=None),
    security_code: str | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_trade_records(
        session,
        user_id=int(current_user.id),
        start_date=start_date,
        end_date=end_date,
        source=source,
        security_code=security_code,
        limit=limit,
        offset=offset,
    )


@router.get("/fund-flows")
def get_local_fund_flow_records(
    start_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    flow_category: str | None = Query(default=None),
    security_code: str | None = Query(default=None),
    security_name: str | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_fund_flow_records(
        session,
        user_id=int(current_user.id),
        start_date=start_date,
        end_date=end_date,
        flow_category=flow_category,
        security_code=security_code,
        security_name=security_name,
        limit=limit,
        offset=offset,
    )


@router.get("/fund-flow-categories")
def get_local_fund_flow_categories(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"items": list_fund_flow_categories(session, user_id=int(current_user.id))}


@router.get("/fund-flow-filter-options")
def get_local_fund_flow_filter_options(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_fund_flow_filter_options(session, user_id=int(current_user.id))


@router.get("/sync-runs")
def get_eastmoney_sync_runs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"items": list_sync_runs(session, user_id=int(current_user.id), limit=limit)}


@router.get("/asset-snapshot/latest")
def get_latest_eastmoney_asset_snapshot(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return {"item": get_latest_asset_snapshot(session, user_id=int(current_user.id))}


@router.get("/positions/latest")
def get_latest_eastmoney_positions(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    return list_latest_position_snapshots(session, user_id=int(current_user.id))


@router.get("/market-quotes/latest")
def get_latest_eastmoney_market_quotes(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    items = list_latest_market_quotes(session, user_id=int(current_user.id))
    return {"items": [serialize_quote_item(item) for item in items]}


@router.post("/market-quotes/refresh")
def refresh_eastmoney_market_quotes(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
):
    try:
        result = refresh_market_quotes_from_akshare(session, user_id=int(current_user.id))
        return serialize_quote_refresh_result(result)
    except Exception as exc:
        items = list_latest_market_quotes(session, user_id=int(current_user.id))
        return {
            "provider": "akshare",
            "database_path": str(get_market_data_db_path()),
            "target_count": len(items),
            "refreshed_count": 0,
            "error_count": 1,
            "error": f"刷新 AKShare 行情失败：{exc}",
            "items": [serialize_quote_item(item) for item in items],
        }
