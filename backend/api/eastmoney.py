from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Session

from backend.core.auth import get_current_active_user
from backend.core.feature_access_guard import require_feature_access_dependency
from backend.core.ocr_preview import OcrPreviewError, run_paddle_ocr_preview
from backend.core.stock import (
    EastmoneyTradeError,
    analyze_qlib_daily_target,
    backtest_qlib_one_lot_score_strategy,
    backtest_hk_pool_one_lot_score,
    export_qlib_daily_dataset,
    fetch_akshare_etf_intraday,
    fetch_akshare_stock_history,
    get_market_data_db_path,
    get_latest_asset_snapshot,
    import_mobile_trade_detail_record,
    list_latest_market_quotes,
    list_fund_flow_categories,
    list_fund_flow_filter_options,
    list_fund_flow_records,
    list_latest_position_snapshots,
    list_sync_runs,
    list_trade_records,
    open_trade_account_page,
    read_trade_snapshot,
    refresh_market_quotes_from_akshare,
    refresh_eastmoney_sheet_workbook,
    serialize_quote_item,
    serialize_quote_refresh_result,
    serialize_akshare_etf_intraday,
    serialize_akshare_stock_history,
    serialize_qlib_export_result,
    serialize_qlib_factor_analysis,
    serialize_qlib_backtest_result,
    serialize_qlib_pool_backtest_result,
    screen_hk_pool,
    serialize_qlib_screen_result,
    snapshot_to_dict,
    sync_trade_data,
)
from backend.core.stock.market_data import (
    MARKET_DATA_PROVIDER_AKSHARE,
    MarketHistoryTarget,
    connect_market_data_db,
    normalize_autype,
    normalize_ktype,
    normalize_market_code,
    upsert_kline_rows,
)
from backend.core.stock.akshare_market import (
    AkshareEtfIntraday,
    AkshareStockHistory,
    AkshareStockHistoryRow,
    _normalize_ohlc_prices,
)
from backend.core.stock.eastmoney_ocr import parse_mobile_trade_detail_from_ocr_document
from backend.db import get_session
from backend.models import User


router = APIRouter(
    dependencies=[Depends(require_feature_access_dependency("notes.eastmoney"))],
)


class EastmoneySyncRequest(BaseModel):
    start_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end_date: str | None = PydanticField(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


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
    take_profit_percent: float = Query(default=5.0, ge=0, le=100),
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
                take_profit_percent=take_profit_percent,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
                refresh=refresh,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取 Qlib 回测失败：{exc}") from exc


@router.get("/qlib/backtest/hk-pool-one-lot-score")
def get_eastmoney_qlib_hk_pool_one_lot_score_backtest(
    refresh: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=5000),
    detail_limit: int = Query(default=300, ge=1, le=5000),
    start_date: str = Query(default="2025-01-01"),
    end_date: str | None = Query(default=None),
    score_threshold: int = Query(default=84, ge=0, le=100),
    take_profit_percent: float = Query(default=5.0, ge=0, le=100),
    cost_rate: float = Query(default=0.01, ge=0, le=1),
    force_liquidate_end: bool = Query(default=True),
):
    try:
        return serialize_qlib_pool_backtest_result(
            backtest_hk_pool_one_lot_score(
                refresh=refresh,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                score_threshold=score_threshold,
                take_profit_percent=take_profit_percent,
                cost_rate=cost_rate,
                force_liquidate_end=force_liquidate_end,
            ),
            detail_limit=detail_limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"读取港股池 Qlib 回测失败：{exc}") from exc


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
):
    try:
        intraday = fetch_akshare_etf_intraday(
            market=market,
            symbol=symbol,
            name=name,
            trade_date=trade_date,
            period=period,
            day_count=day_count,
        )
        return serialize_akshare_etf_intraday(intraday)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        market_code, normalized_symbol = resolve_akshare_market_symbol(format_market_symbol(market, symbol))
        return serialize_akshare_etf_intraday(
            AkshareEtfIntraday(
                provider="akshare-error",
                market=market_code,
                symbol=normalized_symbol,
                name=name,
                period=period,
                trade_date=trade_date or "",
                rows=(),
                error=str(exc),
            )
        )


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
