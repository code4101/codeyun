from __future__ import annotations

import datetime as dt
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


DEFAULT_AKSHARE_SYMBOL = "159278"
DEFAULT_AKSHARE_SECURITY_NAME = "机器人PH"
DEFAULT_AKSHARE_START_DATE = "20250812"
DEFAULT_AKSHARE_PERIOD = "daily"
DEFAULT_AKSHARE_MARKET = "SZ"

AKSHARE_PERIODS = {"daily", "weekly", "monthly", "quarterly", "yearly"}
AKSHARE_ADJUSTS = {"", "qfq", "hfq"}
AKSHARE_NATIVE_HISTORY_PERIODS = {"daily", "weekly", "monthly"}
AKSHARE_MINUTE_PERIODS = {"1", "5", "15", "30", "60", "120"}
AKSHARE_NATIVE_MINUTE_PERIODS = {"1", "5", "15", "30", "60"}
AKSHARE_MARKETS = {"SH", "SZ", "HK"}


@dataclass(frozen=True)
class AkshareStockHistoryRow:
    date: str
    symbol: str
    open: float | None
    close: float | None
    high: float | None
    low: float | None
    volume: float | None
    amount: float | None
    amplitude: float | None
    change_percent: float | None
    change_amount: float | None
    turnover_rate: float | None


@dataclass(frozen=True)
class AkshareStockHistory:
    provider: str
    market: str
    symbol: str
    name: str
    period: str
    adjust: str
    start_date: str
    end_date: str
    rows: tuple[AkshareStockHistoryRow, ...]
    error: str = ""


@dataclass(frozen=True)
class AkshareEtfIntradayRow:
    time: str
    symbol: str
    open: float | None
    close: float | None
    high: float | None
    low: float | None
    volume: float | None
    amount: float | None
    average_price: float | None


@dataclass(frozen=True)
class AkshareEtfIntraday:
    provider: str
    market: str
    symbol: str
    name: str
    period: str
    trade_date: str
    rows: tuple[AkshareEtfIntradayRow, ...]
    error: str = ""


def fetch_akshare_stock_history(
    *,
    market: str = DEFAULT_AKSHARE_MARKET,
    symbol: str = DEFAULT_AKSHARE_SYMBOL,
    name: str = DEFAULT_AKSHARE_SECURITY_NAME,
    period: str = DEFAULT_AKSHARE_PERIOD,
    start_date: str = DEFAULT_AKSHARE_START_DATE,
    end_date: str | None = None,
    adjust: str = "",
) -> AkshareStockHistory:
    normalized_market = _normalize_market(market, symbol)
    normalized_symbol = _normalize_symbol(symbol, market=normalized_market)
    normalized_period = _normalize_period(period)
    normalized_adjust = _normalize_adjust(adjust)
    request_period = "monthly" if normalized_period in {"quarterly", "yearly"} else normalized_period
    normalized_start = _normalize_akshare_date(start_date) or DEFAULT_AKSHARE_START_DATE
    normalized_end = _normalize_akshare_date(end_date) or dt.date.today().strftime("%Y%m%d")

    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("AKShare 未安装或无法导入，请先执行 uv add akshare") from exc

    try:
        rows = _request_akshare_history_rows(
            ak,
            market=normalized_market,
            symbol=normalized_symbol,
            period=request_period,
            start_date=normalized_start,
            end_date=normalized_end,
            adjust=normalized_adjust,
        )
    except Exception as exc:
        if _looks_like_proxy_error(exc):
            try:
                with _without_proxy_env():
                    rows = _request_akshare_history_rows(
                        ak,
                        market=normalized_market,
                        symbol=normalized_symbol,
                        period=request_period,
                        start_date=normalized_start,
                        end_date=normalized_end,
                        adjust=normalized_adjust,
                    )
            except Exception as retry_exc:
                try:
                    rows = _request_akshare_history_rows_with_curl_transport(
                        ak,
                        market=normalized_market,
                        symbol=normalized_symbol,
                        period=request_period,
                        start_date=normalized_start,
                        end_date=normalized_end,
                        adjust=normalized_adjust,
                    )
                except Exception as transport_exc:
                    raise RuntimeError(f"AKShare 获取 {normalized_symbol} 历史行情失败：{transport_exc}") from transport_exc
        else:
            try:
                rows = _request_akshare_history_rows_with_curl_transport(
                    ak,
                    market=normalized_market,
                    symbol=normalized_symbol,
                    period=request_period,
                    start_date=normalized_start,
                    end_date=normalized_end,
                    adjust=normalized_adjust,
                )
            except Exception as transport_exc:
                raise RuntimeError(f"AKShare 获取 {normalized_symbol} 历史行情失败：{transport_exc}") from transport_exc

    if normalized_period == "quarterly":
        rows = _aggregate_history_rows(rows, normalized_symbol, "quarterly")
    elif normalized_period == "yearly":
        rows = _aggregate_history_rows(rows, normalized_symbol, "yearly")

    return AkshareStockHistory(
        provider="akshare",
        market=normalized_market,
        symbol=normalized_symbol,
        name=name.strip() or DEFAULT_AKSHARE_SECURITY_NAME,
        period=normalized_period,
        adjust=normalized_adjust,
        start_date=normalized_start,
        end_date=normalized_end,
        rows=rows,
    )


def fetch_akshare_etf_intraday(
    *,
    market: str = DEFAULT_AKSHARE_MARKET,
    symbol: str = DEFAULT_AKSHARE_SYMBOL,
    name: str = DEFAULT_AKSHARE_SECURITY_NAME,
    trade_date: str | None = None,
    period: str = "1",
    day_count: int = 1,
) -> AkshareEtfIntraday:
    normalized_market = _normalize_market(market, symbol)
    normalized_symbol = _normalize_symbol(symbol, market=normalized_market)
    normalized_period = _normalize_minute_period(period)
    request_period = normalized_period if normalized_period in AKSHARE_NATIVE_MINUTE_PERIODS else "60"
    normalized_trade_date = _normalize_akshare_date(trade_date) or dt.date.today().strftime("%Y%m%d")
    normalized_day_count = max(1, min(int(day_count or 1), 5))
    trade_day = dt.date.fromisoformat(f"{normalized_trade_date[:4]}-{normalized_trade_date[4:6]}-{normalized_trade_date[6:8]}")
    start_day = trade_day - dt.timedelta(days=10 if normalized_day_count > 1 else 0)
    start_at = f"{start_day.isoformat()} 09:30:00"
    close_at = "16:00:00" if normalized_market == "HK" else "15:00:00"
    end_at = f"{trade_day.isoformat()} {close_at}"

    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("AKShare 未安装或无法导入，请先执行 uv sync") from exc

    try:
        rows = _request_akshare_etf_intraday_rows(
            ak,
            market=normalized_market,
            symbol=normalized_symbol,
            start_date=start_at,
            end_date=end_at,
            period=request_period,
        )
    except Exception as exc:
        try:
            rows = _request_akshare_etf_intraday_rows_with_curl_transport(
                ak,
                market=normalized_market,
                symbol=normalized_symbol,
                start_date=start_at,
                end_date=end_at,
                period=request_period,
            )
        except Exception as transport_exc:
            raise RuntimeError(f"AKShare 获取 {normalized_symbol} 分时行情失败：{transport_exc}") from transport_exc

    rows = _limit_intraday_rows_to_recent_days(rows, normalized_day_count)
    if normalized_period == "120":
        rows = _aggregate_intraday_rows(rows, normalized_symbol, minutes=120)

    return AkshareEtfIntraday(
        provider="akshare",
        market=normalized_market,
        symbol=normalized_symbol,
        name=name.strip() or DEFAULT_AKSHARE_SECURITY_NAME,
        period=normalized_period,
        trade_date=normalized_trade_date,
        rows=rows,
    )


def serialize_akshare_stock_history(history: AkshareStockHistory) -> dict[str, Any]:
    return {
        "provider": history.provider,
        "market": history.market,
        "symbol": history.symbol,
        "name": history.name,
        "period": history.period,
        "adjust": history.adjust,
        "start_date": history.start_date,
        "end_date": history.end_date,
        "error": history.error,
        "items": [
            {
                "date": row.date,
                "symbol": row.symbol,
                "open": row.open,
                "close": row.close,
                "high": row.high,
                "low": row.low,
                "volume": row.volume,
                "amount": row.amount,
                "amplitude": row.amplitude,
                "change_percent": row.change_percent,
                "change_amount": row.change_amount,
                "turnover_rate": row.turnover_rate,
            }
            for row in history.rows
        ],
    }


def serialize_akshare_etf_intraday(intraday: AkshareEtfIntraday) -> dict[str, Any]:
    return {
        "provider": intraday.provider,
        "market": intraday.market,
        "symbol": intraday.symbol,
        "name": intraday.name,
        "period": intraday.period,
        "trade_date": intraday.trade_date,
        "error": intraday.error,
        "items": [
            {
                "time": row.time,
                "symbol": row.symbol,
                "open": row.open,
                "close": row.close,
                "high": row.high,
                "low": row.low,
                "volume": row.volume,
                "amount": row.amount,
                "average_price": row.average_price,
            }
            for row in intraday.rows
        ],
    }


def _request_akshare_history_rows(
    ak_module: Any,
    *,
    market: str,
    symbol: str,
    period: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> tuple[AkshareStockHistoryRow, ...]:
    if market == "HK":
        try:
            frame = ak_module.stock_hk_hist(
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        except Exception:
            return _request_akshare_hk_daily_rows_from_sina(
                ak_module,
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
    else:
        frame = ak_module.stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    return tuple(_history_row_from_record(symbol, record) for record in frame.to_dict("records"))


def _request_akshare_hk_daily_rows_from_sina(
    ak_module: Any,
    *,
    symbol: str,
    period: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> tuple[AkshareStockHistoryRow, ...]:
    frame = ak_module.stock_hk_daily(symbol=symbol, adjust=adjust if adjust in {"", "qfq"} else "")
    start_iso = _akshare_date_to_iso(start_date)
    end_iso = _akshare_date_to_iso(end_date)
    rows = tuple(
        _history_row_from_record(symbol, record)
        for record in frame.to_dict("records")
        if _date_in_range(str(record.get("date") or record.get("日期") or ""), start_iso, end_iso)
    )
    if period in {"weekly", "monthly"}:
        return _aggregate_history_rows(rows, symbol, period)
    return rows


def _request_akshare_etf_intraday_rows(
    ak_module: Any,
    *,
    market: str,
    symbol: str,
    start_date: str,
    end_date: str,
    period: str,
) -> tuple[AkshareEtfIntradayRow, ...]:
    if market == "HK":
        frame = ak_module.stock_hk_hist_min_em(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust="",
        )
    else:
        frame = ak_module.fund_etf_hist_min_em(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            period=period,
            adjust="",
        )
    return tuple(_intraday_row_from_record(symbol, record) for record in frame.to_dict("records"))


def _request_akshare_etf_intraday_rows_with_curl_transport(
    ak_module: Any,
    *,
    market: str,
    symbol: str,
    start_date: str,
    end_date: str,
    period: str,
) -> tuple[AkshareEtfIntradayRow, ...]:
    try:
        with _with_curl_cffi_requests_get():
            return _request_akshare_etf_intraday_rows(
                ak_module,
                market=market,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )
    except Exception:
        with _without_proxy_env(), _with_curl_cffi_requests_get():
            return _request_akshare_etf_intraday_rows(
                ak_module,
                market=market,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                period=period,
            )


def _request_akshare_history_rows_with_curl_transport(
    ak_module: Any,
    *,
    market: str,
    symbol: str,
    period: str,
    start_date: str,
    end_date: str,
    adjust: str,
) -> tuple[AkshareStockHistoryRow, ...]:
    try:
        with _with_curl_cffi_requests_get():
            return _request_akshare_history_rows(
                ak_module,
                market=market,
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
    except Exception:
        with _without_proxy_env(), _with_curl_cffi_requests_get():
            return _request_akshare_history_rows(
                ak_module,
                market=market,
                symbol=symbol,
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )


def _history_row_from_record(symbol: str, record: dict[str, Any]) -> AkshareStockHistoryRow:
    open_price = _float_or_none(record.get("开盘") if "开盘" in record else record.get("open"))
    close_price = _float_or_none(record.get("收盘") if "收盘" in record else record.get("close"))
    high_price = _float_or_none(record.get("最高") if "最高" in record else record.get("high"))
    low_price = _float_or_none(record.get("最低") if "最低" in record else record.get("low"))
    open_price, close_price, high_price, low_price = _normalize_ohlc_prices(
        open_price,
        close_price,
        high_price,
        low_price,
    )
    return AkshareStockHistoryRow(
        date=str(record.get("日期") or record.get("date") or ""),
        symbol=str(record.get("股票代码") or symbol),
        open=open_price,
        close=close_price,
        high=high_price,
        low=low_price,
        volume=_float_or_none(record.get("成交量") if "成交量" in record else record.get("volume")),
        amount=_float_or_none(record.get("成交额") if "成交额" in record else record.get("amount")),
        amplitude=_float_or_none(record.get("振幅") if "振幅" in record else record.get("amplitude")),
        change_percent=_float_or_none(record.get("涨跌幅") if "涨跌幅" in record else record.get("change_percent")),
        change_amount=_float_or_none(record.get("涨跌额") if "涨跌额" in record else record.get("change_amount")),
        turnover_rate=_float_or_none(record.get("换手率") if "换手率" in record else record.get("turnover_rate")),
    )


def _normalize_ohlc_prices(
    open_price: float | None,
    close_price: float | None,
    high_price: float | None,
    low_price: float | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    valid_prices = [value for value in (open_price, close_price, high_price, low_price) if value is not None and value > 0]
    fallback = close_price if close_price is not None and close_price > 0 else (valid_prices[0] if valid_prices else None)

    def normalize(value: float | None) -> float | None:
        if value is not None and value > 0:
            return value
        return fallback

    open_price = normalize(open_price)
    close_price = normalize(close_price)
    high_price = normalize(high_price)
    low_price = normalize(low_price)

    price_bounds = [value for value in (open_price, close_price, high_price, low_price) if value is not None and value > 0]
    if price_bounds:
        high_price = max(price_bounds)
        low_price = min(price_bounds)
    return open_price, close_price, high_price, low_price


def _intraday_row_from_record(symbol: str, record: dict[str, Any]) -> AkshareEtfIntradayRow:
    return AkshareEtfIntradayRow(
        time=str(record.get("时间") or ""),
        symbol=symbol,
        open=_float_or_none(record.get("开盘")),
        close=_float_or_none(record.get("收盘")),
        high=_float_or_none(record.get("最高")),
        low=_float_or_none(record.get("最低")),
        volume=_float_or_none(record.get("成交量")),
        amount=_float_or_none(record.get("成交额")),
        average_price=_float_or_none(record.get("均价") if "均价" in record else record.get("最新价")),
    )


def _aggregate_history_rows(
    rows: tuple[AkshareStockHistoryRow, ...],
    symbol: str,
    period: str,
) -> tuple[AkshareStockHistoryRow, ...]:
    groups: dict[str, list[AkshareStockHistoryRow]] = {}
    for row in rows:
        try:
            date_value = dt.date.fromisoformat(row.date[:10])
        except ValueError:
            continue
        if period == "weekly":
            iso_year, iso_week, _weekday = date_value.isocalendar()
            group_key = f"{iso_year}-W{iso_week:02d}"
        elif period == "monthly":
            group_key = f"{date_value.year}-{date_value.month:02d}"
        elif period == "quarterly":
            group_key = f"{date_value.year}-Q{(date_value.month - 1) // 3 + 1}"
        else:
            group_key = str(date_value.year)
        groups.setdefault(group_key, []).append(row)

    aggregated: list[AkshareStockHistoryRow] = []
    for group_key, group_rows in groups.items():
        ordered = sorted(group_rows, key=lambda item: item.date)
        first = ordered[0]
        last = ordered[-1]
        high = _max_or_none(row.high for row in ordered)
        low = _min_or_none(row.low for row in ordered)
        volume = _sum_or_none(row.volume for row in ordered)
        amount = _sum_or_none(row.amount for row in ordered)
        change_amount = None if first.open is None or last.close is None else last.close - first.open
        change_percent = None
        if change_amount is not None and first.open not in (None, 0):
            change_percent = change_amount / first.open * 100
        aggregated.append(
            AkshareStockHistoryRow(
                date=last.date if period in {"weekly", "monthly"} else group_key,
                symbol=symbol,
                open=first.open,
                close=last.close,
                high=high,
                low=low,
                volume=volume,
                amount=amount,
                amplitude=None,
                change_percent=change_percent,
                change_amount=change_amount,
                turnover_rate=_sum_or_none(row.turnover_rate for row in ordered),
            )
        )
    return tuple(aggregated)


def _akshare_date_to_iso(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return str(value or "")[:10]


def _date_in_range(value: str, start_iso: str, end_iso: str) -> bool:
    date_text = _akshare_date_to_iso(value)
    return bool(date_text) and (not start_iso or date_text >= start_iso) and (not end_iso or date_text <= end_iso)


def _limit_intraday_rows_to_recent_days(
    rows: tuple[AkshareEtfIntradayRow, ...],
    day_count: int,
) -> tuple[AkshareEtfIntradayRow, ...]:
    if day_count <= 1:
        return rows
    dates = sorted({row.time[:10] for row in rows if len(row.time) >= 10})
    keep_dates = set(dates[-day_count:])
    return tuple(row for row in rows if row.time[:10] in keep_dates)


def _aggregate_intraday_rows(
    rows: tuple[AkshareEtfIntradayRow, ...],
    symbol: str,
    *,
    minutes: int,
) -> tuple[AkshareEtfIntradayRow, ...]:
    grouped: dict[tuple[str, int], list[AkshareEtfIntradayRow]] = {}
    for row in rows:
        try:
            time_value = dt.datetime.fromisoformat(row.time)
        except ValueError:
            continue
        market_open = time_value.replace(hour=9, minute=30, second=0, microsecond=0)
        offset = int((time_value - market_open).total_seconds() // 60)
        grouped.setdefault((time_value.date().isoformat(), max(0, offset) // minutes), []).append(row)

    aggregated: list[AkshareEtfIntradayRow] = []
    for (_date_key, _bucket), group_rows in sorted(grouped.items()):
        ordered = sorted(group_rows, key=lambda item: item.time)
        first = ordered[0]
        last = ordered[-1]
        aggregated.append(
            AkshareEtfIntradayRow(
                time=first.time,
                symbol=symbol,
                open=first.open,
                close=last.close,
                high=_max_or_none(row.high for row in ordered),
                low=_min_or_none(row.low for row in ordered),
                volume=_sum_or_none(row.volume for row in ordered),
                amount=_sum_or_none(row.amount for row in ordered),
                average_price=last.average_price,
            )
        )
    return tuple(aggregated)


def _normalize_market(value: str | None, symbol: str | None = None) -> str:
    market = str(value or "").strip().upper()
    if not market and str(symbol or "").strip().upper().startswith("HK."):
        market = "HK"
    if market in {"HKG"}:
        market = "HK"
    if market in {"SHA", "SSE"}:
        market = "SH"
    if market in {"SZA", "SZSE"}:
        market = "SZ"
    if not market:
        market = DEFAULT_AKSHARE_MARKET
    if market not in AKSHARE_MARKETS:
        raise ValueError(f"暂不支持的 AKShare 市场：{value}")
    return market


def _normalize_symbol(value: str | None, *, market: str = DEFAULT_AKSHARE_MARKET) -> str:
    text = str(value or "").strip()
    if "." in text:
        text = text.rsplit(".", 1)[1]
    symbol = "".join(ch for ch in text if ch.isalnum())
    if not symbol:
        raise ValueError("股票代码不能为空")
    if market == "HK":
        return symbol.zfill(5)
    return symbol.zfill(6)


def _normalize_period(value: str | None) -> str:
    period = (value or DEFAULT_AKSHARE_PERIOD).strip().lower()
    if period not in AKSHARE_PERIODS:
        raise ValueError(f"暂不支持的 AKShare 周期：{value}")
    return period


def _normalize_minute_period(value: str | None) -> str:
    period = (value or "1").strip()
    if period not in AKSHARE_MINUTE_PERIODS:
        raise ValueError(f"暂不支持的 AKShare 分钟周期：{value}")
    return period


def _normalize_adjust(value: str | None) -> str:
    adjust = (value or "").strip().lower()
    if adjust not in AKSHARE_ADJUSTS:
        raise ValueError(f"暂不支持的 AKShare 复权类型：{value}")
    return adjust


def _normalize_akshare_date(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 8:
        raise ValueError("日期格式必须是 YYYY-MM-DD 或 YYYYMMDD")
    dt.date.fromisoformat(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}")
    return digits


def _looks_like_proxy_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return "proxyerror" in text or "unable to connect to proxy" in text


@contextmanager
def _without_proxy_env():
    keys = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _with_curl_cffi_requests_get():
    import requests as requests_module
    from curl_cffi import requests as curl_requests

    original_get = requests_module.get

    def curl_get(url: str, **kwargs: Any):
        kwargs.setdefault("impersonate", "chrome")
        return curl_requests.get(url, **kwargs)

    try:
        requests_module.get = curl_get
        yield
    finally:
        requests_module.get = original_get


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_or_none(values: Any) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return sum(numbers) if numbers else None


def _max_or_none(values: Any) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return max(numbers) if numbers else None


def _min_or_none(values: Any) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return min(numbers) if numbers else None
