from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from backend.core.settings import get_settings


@dataclass(frozen=True)
class IndexBenchmark:
    market: str
    symbol: str
    name: str
    start_date: str
    end_date: str
    start_close: float | None
    end_close: float | None
    return_percent: float | None
    source: str
    error: str = ""


DEFAULT_BENCHMARKS = (
    ("HK", "HSI", "恒生指数"),
    ("HK", "HSCEI", "恒生中国企业指数"),
    ("HK", "HSTECH", "恒生科技指数"),
    ("CN", "sh000001", "上证指数"),
)


def load_index_benchmarks(
    *,
    start_date: str,
    end_date: str | None,
    refresh: bool = False,
) -> tuple[IndexBenchmark, ...]:
    return tuple(
        load_index_benchmark(
            market=market,
            symbol=symbol,
            name=name,
            start_date=start_date,
            end_date=end_date,
            refresh=refresh,
        )
        for market, symbol, name in DEFAULT_BENCHMARKS
    )


def load_index_benchmark(
    *,
    market: str,
    symbol: str,
    name: str,
    start_date: str,
    end_date: str | None,
    refresh: bool = False,
) -> IndexBenchmark:
    start_iso = _normalize_iso_date(start_date)
    end_iso = _normalize_iso_date(end_date) or dt.date.today().isoformat()
    rows, source, error = _load_index_rows(market=market, symbol=symbol, refresh=refresh)
    window = [row for row in rows if start_iso <= row["date"] <= end_iso and row.get("close") is not None]
    if not window:
        return IndexBenchmark(
            market=market,
            symbol=symbol,
            name=name,
            start_date=start_iso,
            end_date=end_iso,
            start_close=None,
            end_close=None,
            return_percent=None,
            source=source,
            error=error or "指数区间数据为空",
        )
    start_close = float(window[0]["close"])
    end_close = float(window[-1]["close"])
    return IndexBenchmark(
        market=market,
        symbol=symbol,
        name=name,
        start_date=window[0]["date"],
        end_date=window[-1]["date"],
        start_close=start_close,
        end_close=end_close,
        return_percent=(end_close / start_close - 1) * 100 if start_close else None,
        source=source,
        error=error,
    )


def load_index_rows(
    *,
    market: str,
    symbol: str,
    refresh: bool = False,
) -> tuple[dict[str, Any], ...]:
    rows, _source, _error = _load_index_rows(market=market, symbol=symbol, refresh=refresh)
    return rows


def serialize_index_benchmark(item: IndexBenchmark) -> dict[str, Any]:
    return {
        "market": item.market,
        "symbol": item.symbol,
        "name": item.name,
        "start_date": item.start_date,
        "end_date": item.end_date,
        "start_close": item.start_close,
        "end_close": item.end_close,
        "return_percent": item.return_percent,
        "source": item.source,
        "error": item.error,
    }


def _load_index_rows(*, market: str, symbol: str, refresh: bool) -> tuple[tuple[dict[str, Any], ...], str, str]:
    cache_path = get_settings().data_dir / "stock" / "index" / f"{market.lower()}_{symbol.lower()}.json"
    if not refresh:
        cached = _read_index_cache(cache_path)
        if cached:
            return cached, "cache:index", ""
    try:
        import akshare as ak

        if market.upper() == "HK":
            frame = ak.stock_hk_index_daily_sina(symbol=symbol)
            source = "akshare:stock_hk_index_daily_sina"
        else:
            frame = ak.stock_zh_index_daily(symbol=symbol)
            source = "akshare:stock_zh_index_daily"
        rows = tuple(
            {
                "date": _normalize_iso_date(row.get("date")),
                "close": _float_or_none(row.get("close")),
            }
            for row in frame.to_dict("records")
        )
        rows = tuple(row for row in rows if row["date"] and row["close"] is not None)
        if rows:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(list(rows), ensure_ascii=False), encoding="utf-8")
        return rows, source, ""
    except Exception as exc:
        cached = _read_index_cache(cache_path)
        if cached:
            return cached, f"cache:index; akshare-error:{exc}", str(exc)
        return (), "akshare-error", str(exc)


def _read_index_cache(path) -> tuple[dict[str, Any], ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(data, list):
        return ()
    rows: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        date = _normalize_iso_date(row.get("date"))
        close = _float_or_none(row.get("close"))
        if date and close is not None:
            rows.append({"date": date, "close": close})
    return tuple(rows)


def _normalize_iso_date(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text[:10]


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number
