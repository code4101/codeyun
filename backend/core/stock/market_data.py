from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func
from sqlmodel import Session, select

from backend.models import EastmoneyPositionSnapshot, EastmoneyTradeRecord

from .akshare_market import fetch_akshare_stock_history


MARKET_DATA_PROVIDER_AKSHARE = "akshare"
DEFAULT_HISTORY_KTYPE = "daily"
DEFAULT_HISTORY_AUTYPE = "none"
DEFAULT_POSITION_LOOKBACK_DAYS = 365

SUPPORTED_KTYPES = {
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
}
SUPPORTED_AUTYPES = {
    "none": "",
    "qfq": "qfq",
    "hfq": "hfq",
}


@dataclass(frozen=True)
class MarketHistoryTarget:
    market: str
    symbol: str
    provider_code: str
    name: str
    sources: tuple[str, ...]
    first_trade_date: str
    start_date: str
    end_date: str


@dataclass(frozen=True)
class MarketHistorySyncItem:
    target: MarketHistoryTarget
    ktype: str
    autype: str
    requested_start: str
    requested_end: str
    fetched_count: int = 0
    inserted_count: int = 0
    updated_count: int = 0
    skipped: bool = False
    error: str = ""


@dataclass(frozen=True)
class MarketHistorySyncResult:
    provider: str
    database_path: Path
    items: tuple[MarketHistorySyncItem, ...]

    @property
    def target_count(self) -> int:
        return len(self.items)

    @property
    def fetched_count(self) -> int:
        return sum(item.fetched_count for item in self.items)

    @property
    def inserted_count(self) -> int:
        return sum(item.inserted_count for item in self.items)

    @property
    def updated_count(self) -> int:
        return sum(item.updated_count for item in self.items)


@dataclass(frozen=True)
class MarketQuoteItem:
    provider: str
    market: str
    symbol: str
    provider_code: str
    name: str
    price: float | None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    prev_close_price: float | None = None
    volume: float | None = None
    turnover: float | None = None
    update_time: str = ""
    fetched_at: float = 0
    raw_json: dict[str, Any] | None = None
    error: str = ""


@dataclass(frozen=True)
class MarketQuoteRefreshResult:
    provider: str
    database_path: Path
    items: tuple[MarketQuoteItem, ...]

    @property
    def target_count(self) -> int:
        return len(self.items)

    @property
    def refreshed_count(self) -> int:
        return sum(1 for item in self.items if item.price is not None and not item.error)

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.items if item.error)


def get_market_data_db_path(data_dir: str | Path | None = None) -> Path:
    if data_dir is None:
        from backend.core.settings import get_settings

        base_dir = get_settings().data_dir
    else:
        base_dir = Path(data_dir)
    return base_dir / "stock" / "market-data.sqlite"


def connect_market_data_db(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else get_market_data_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_market_data_schema(conn)
    return conn


def ensure_market_data_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_kline (
            provider TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            provider_code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            ktype TEXT NOT NULL,
            autype TEXT NOT NULL,
            time_key TEXT NOT NULL,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            turnover REAL,
            pe_ratio REAL,
            turnover_rate REAL,
            change_rate REAL,
            last_close REAL,
            fetched_at REAL NOT NULL,
            provisional INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (provider, market, symbol, ktype, autype, time_key)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_market_kline_symbol_time
        ON market_kline (provider, market, symbol, ktype, autype, time_key)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_quote (
            provider TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            provider_code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            price REAL,
            open_price REAL,
            high_price REAL,
            low_price REAL,
            prev_close_price REAL,
            volume REAL,
            turnover REAL,
            update_time TEXT NOT NULL DEFAULT '',
            fetched_at REAL NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (provider, market, symbol)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_market_quote_provider_code
        ON market_quote (provider, provider_code)
        """
    )
    conn.commit()


def normalize_market_code(market: str | None, symbol: str | None) -> tuple[str, str] | None:
    code = normalize_symbol(symbol)
    if not code:
        return None

    normalized_market = (market or "").strip().upper()
    if normalized_market in {"HK", "HKG"}:
        return "HK", code.zfill(5)
    if normalized_market in {"SH", "SHA", "SSE"}:
        return "SH", code.zfill(6)
    if normalized_market in {"SZ", "SZA", "SZSE"}:
        return "SZ", code.zfill(6)

    if len(code) == 5:
        return "HK", code
    if code.startswith(("5", "6", "9")):
        return "SH", code.zfill(6)
    return "SZ", code.zfill(6)


def normalize_symbol(symbol: str | None) -> str:
    return "".join(ch for ch in (symbol or "").strip().upper() if ch.isalnum())


def to_akshare_code(market: str, symbol: str) -> str:
    normalized = normalize_market_code(market, symbol)
    if normalized is None:
        raise ValueError("股票代码不能为空")
    return normalized[1]


def normalize_ktype(ktype: str | None) -> str:
    value = (ktype or DEFAULT_HISTORY_KTYPE).strip().lower().replace("_", "")
    aliases = {
        "d": "daily",
        "day": "daily",
        "kday": "daily",
        "w": "weekly",
        "week": "weekly",
        "kweek": "weekly",
        "m": "monthly",
        "mon": "monthly",
        "month": "monthly",
        "kmon": "monthly",
    }
    normalized = aliases.get(value, value)
    if normalized not in SUPPORTED_KTYPES:
        raise ValueError(f"AKShare 当前只支持日线/周线/月线，暂不支持的 K 线周期：{ktype}")
    return normalized


def normalize_autype(autype: str | None) -> str:
    value = (autype or DEFAULT_HISTORY_AUTYPE).strip().lower()
    aliases = {
        "": "none",
        "raw": "none",
        "normal": "none",
        "forward": "qfq",
        "before": "qfq",
        "backward": "hfq",
        "after": "hfq",
    }
    normalized = aliases.get(value, value)
    if normalized not in SUPPORTED_AUTYPES:
        raise ValueError(f"暂不支持的复权类型：{autype}")
    return normalized


def collect_market_history_targets(
    session: Session,
    *,
    user_id: int,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_days: int = DEFAULT_POSITION_LOOKBACK_DAYS,
    include_positions: bool = True,
    include_trades: bool = True,
) -> list[MarketHistoryTarget]:
    effective_end_date = normalize_date_text(end_date) or dt.date.today().isoformat()
    fallback_start_date = (
        dt.date.fromisoformat(effective_end_date) - dt.timedelta(days=max(1, int(lookback_days)))
    ).isoformat()
    forced_start_date = normalize_date_text(start_date)

    collected: dict[tuple[str, str], dict[str, Any]] = {}

    def add_security(
        *,
        market: str | None,
        symbol: str | None,
        name: str | None,
        source: str,
        trade_date: str | None = None,
    ) -> None:
        normalized = normalize_market_code(market, symbol)
        if normalized is None:
            return
        normalized_market, normalized_symbol = normalized
        key = (normalized_market, normalized_symbol)
        item = collected.setdefault(
            key,
            {
                "market": normalized_market,
                "symbol": normalized_symbol,
                "name": "",
                "sources": set(),
                "first_trade_date": "",
            },
        )
        if name and not item["name"]:
            item["name"] = str(name).strip()
        item["sources"].add(source)
        normalized_trade_date = normalize_date_text(trade_date)
        if normalized_trade_date and (
            not item["first_trade_date"] or normalized_trade_date < item["first_trade_date"]
        ):
            item["first_trade_date"] = normalized_trade_date

    if include_positions:
        latest_captured_at = session.exec(
            select(func.max(EastmoneyPositionSnapshot.captured_at)).where(
                EastmoneyPositionSnapshot.user_id == user_id,
            )
        ).one()
        if latest_captured_at is not None:
            positions = session.exec(
                select(EastmoneyPositionSnapshot).where(
                    EastmoneyPositionSnapshot.user_id == user_id,
                    EastmoneyPositionSnapshot.captured_at == latest_captured_at,
                )
            ).all()
            for position in positions:
                add_security(
                    market=position.market,
                    symbol=position.security_code,
                    name=position.security_name,
                    source=f"position:{position.source}",
                )

    if include_trades:
        trades = session.exec(
            select(
                EastmoneyTradeRecord.market,
                EastmoneyTradeRecord.security_code,
                EastmoneyTradeRecord.security_name,
                EastmoneyTradeRecord.trade_date,
                EastmoneyTradeRecord.source,
            )
            .where(EastmoneyTradeRecord.user_id == user_id)
            .order_by(EastmoneyTradeRecord.trade_date)
        ).all()
        for market, security_code, security_name, trade_date, source in trades:
            add_security(
                market=market,
                symbol=security_code,
                name=security_name,
                source=f"trade:{source}",
                trade_date=trade_date,
            )

    targets: list[MarketHistoryTarget] = []
    for item in collected.values():
        first_trade_date = item["first_trade_date"]
        target_start_date = forced_start_date or first_trade_date or fallback_start_date
        market = item["market"]
        symbol = item["symbol"]
        targets.append(
            MarketHistoryTarget(
                market=market,
                symbol=symbol,
                provider_code=to_akshare_code(market, symbol),
                name=item["name"],
                sources=tuple(sorted(item["sources"])),
                first_trade_date=first_trade_date,
                start_date=target_start_date,
                end_date=effective_end_date,
            )
        )

    return sorted(targets, key=lambda target: (target.market, target.symbol))


def normalize_date_text(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return dt.date.fromisoformat(text[:10]).isoformat()


def normalize_time_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) >= 10:
        try:
            return dt.date.fromisoformat(text[:10]).isoformat() + text[10:]
        except ValueError:
            return text
    return text


def get_incremental_start_date(
    conn: sqlite3.Connection,
    *,
    provider: str,
    target: MarketHistoryTarget,
    ktype: str,
    autype: str,
    requested_start: str,
    overlap_days: int = 1,
) -> str:
    row = conn.execute(
        """
        SELECT MAX(time_key) AS max_time_key
        FROM market_kline
        WHERE provider = ?
          AND market = ?
          AND symbol = ?
          AND ktype = ?
          AND autype = ?
        """,
        (provider, target.market, target.symbol, ktype, autype),
    ).fetchone()
    max_time_key = str(row["max_time_key"] or "") if row else ""
    if not max_time_key:
        return requested_start

    latest_date = dt.date.fromisoformat(max_time_key[:10])
    incremental_start = (latest_date - dt.timedelta(days=max(0, int(overlap_days)))).isoformat()
    return max(requested_start, incremental_start)


def upsert_kline_rows(
    conn: sqlite3.Connection,
    *,
    provider: str,
    target: MarketHistoryTarget,
    ktype: str,
    autype: str,
    rows: Iterable[dict[str, Any]],
    provisional_date: str | None = None,
) -> tuple[int, int]:
    now = time.time()
    provisional_threshold = normalize_date_text(provisional_date) if provisional_date else dt.date.today().isoformat()
    inserted_count = 0
    updated_count = 0

    for raw_row in rows:
        time_key = normalize_time_key(raw_row.get("time_key") or raw_row.get("time") or raw_row.get("date"))
        if not time_key:
            continue
        exists = conn.execute(
            """
            SELECT 1
            FROM market_kline
            WHERE provider = ?
              AND market = ?
              AND symbol = ?
              AND ktype = ?
              AND autype = ?
              AND time_key = ?
            """,
            (provider, target.market, target.symbol, ktype, autype, time_key),
        ).fetchone()
        if exists:
            updated_count += 1
        else:
            inserted_count += 1

        conn.execute(
            """
            INSERT INTO market_kline (
                provider, market, symbol, provider_code, name, ktype, autype, time_key,
                open, close, high, low, volume, turnover, pe_ratio, turnover_rate,
                change_rate, last_close, fetched_at, provisional, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, market, symbol, ktype, autype, time_key)
            DO UPDATE SET
                provider_code = excluded.provider_code,
                name = excluded.name,
                open = excluded.open,
                close = excluded.close,
                high = excluded.high,
                low = excluded.low,
                volume = excluded.volume,
                turnover = excluded.turnover,
                pe_ratio = excluded.pe_ratio,
                turnover_rate = excluded.turnover_rate,
                change_rate = excluded.change_rate,
                last_close = excluded.last_close,
                fetched_at = excluded.fetched_at,
                provisional = excluded.provisional,
                raw_json = excluded.raw_json
            """,
            (
                provider,
                target.market,
                target.symbol,
                target.provider_code,
                target.name,
                ktype,
                autype,
                time_key,
                _float_or_none(raw_row.get("open")),
                _float_or_none(raw_row.get("close")),
                _float_or_none(raw_row.get("high")),
                _float_or_none(raw_row.get("low")),
                _float_or_none(raw_row.get("volume")),
                _float_or_none(raw_row.get("turnover")),
                _float_or_none(raw_row.get("pe_ratio")),
                _float_or_none(raw_row.get("turnover_rate")),
                _float_or_none(raw_row.get("change_rate")),
                _float_or_none(raw_row.get("last_close")),
                now,
                1 if time_key[:10] >= provisional_threshold else 0,
                json.dumps(_json_safe_dict(raw_row), ensure_ascii=False, sort_keys=True),
            ),
        )

    conn.commit()
    return inserted_count, updated_count


def upsert_quote_items(conn: sqlite3.Connection, *, items: Iterable[MarketQuoteItem]) -> tuple[int, int]:
    inserted_count = 0
    updated_count = 0

    for item in items:
        if item.error:
            continue
        exists = conn.execute(
            """
            SELECT 1
            FROM market_quote
            WHERE provider = ?
              AND market = ?
              AND symbol = ?
            """,
            (item.provider, item.market, item.symbol),
        ).fetchone()
        if exists:
            updated_count += 1
        else:
            inserted_count += 1

        conn.execute(
            """
            INSERT INTO market_quote (
                provider, market, symbol, provider_code, name, price, open_price,
                high_price, low_price, prev_close_price, volume, turnover,
                update_time, fetched_at, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, market, symbol)
            DO UPDATE SET
                provider_code = excluded.provider_code,
                name = excluded.name,
                price = excluded.price,
                open_price = excluded.open_price,
                high_price = excluded.high_price,
                low_price = excluded.low_price,
                prev_close_price = excluded.prev_close_price,
                volume = excluded.volume,
                turnover = excluded.turnover,
                update_time = excluded.update_time,
                fetched_at = excluded.fetched_at,
                raw_json = excluded.raw_json
            """,
            (
                item.provider,
                item.market,
                item.symbol,
                item.provider_code,
                item.name,
                item.price,
                item.open_price,
                item.high_price,
                item.low_price,
                item.prev_close_price,
                item.volume,
                item.turnover,
                item.update_time,
                item.fetched_at,
                json.dumps(item.raw_json or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    conn.commit()
    return inserted_count, updated_count


def sync_market_history_from_akshare(
    session: Session,
    *,
    user_id: int,
    database_path: str | Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_days: int = DEFAULT_POSITION_LOOKBACK_DAYS,
    ktype: str = DEFAULT_HISTORY_KTYPE,
    autype: str = DEFAULT_HISTORY_AUTYPE,
    include_positions: bool = True,
    include_trades: bool = True,
    incremental: bool = True,
    dry_run: bool = False,
    limit: int | None = None,
) -> MarketHistorySyncResult:
    normalized_ktype = normalize_ktype(ktype)
    normalized_autype = normalize_autype(autype)
    targets = collect_market_history_targets(
        session,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        lookback_days=lookback_days,
        include_positions=include_positions,
        include_trades=include_trades,
    )
    if limit is not None:
        targets = targets[: max(0, int(limit))]

    db_path = Path(database_path) if database_path is not None else get_market_data_db_path()
    items: list[MarketHistorySyncItem] = []
    if dry_run:
        return MarketHistorySyncResult(
            provider=MARKET_DATA_PROVIDER_AKSHARE,
            database_path=db_path,
            items=tuple(
                MarketHistorySyncItem(
                    target=target,
                    ktype=normalized_ktype,
                    autype=normalized_autype,
                    requested_start=target.start_date,
                    requested_end=target.end_date,
                    skipped=True,
                )
                for target in targets
            ),
        )

    with connect_market_data_db(db_path) as conn:
        for target in targets:
            requested_start = (
                get_incremental_start_date(
                    conn,
                    provider=MARKET_DATA_PROVIDER_AKSHARE,
                    target=target,
                    ktype=normalized_ktype,
                    autype=normalized_autype,
                    requested_start=target.start_date,
                )
                if incremental
                else target.start_date
            )
            if requested_start > target.end_date:
                items.append(
                    MarketHistorySyncItem(
                        target=target,
                        ktype=normalized_ktype,
                        autype=normalized_autype,
                        requested_start=requested_start,
                        requested_end=target.end_date,
                        skipped=True,
                    )
                )
                continue

            try:
                history = fetch_akshare_stock_history(
                    symbol=target.provider_code,
                    name=target.name,
                    period=normalized_ktype,
                    start_date=requested_start,
                    end_date=target.end_date,
                    adjust=SUPPORTED_AUTYPES[normalized_autype],
                )
                rows = [_kline_row_from_akshare_history_row(row) for row in history.rows]
                inserted_count, updated_count = upsert_kline_rows(
                    conn,
                    provider=MARKET_DATA_PROVIDER_AKSHARE,
                    target=target,
                    ktype=normalized_ktype,
                    autype=normalized_autype,
                    rows=rows,
                    provisional_date=target.end_date,
                )
                items.append(
                    MarketHistorySyncItem(
                        target=target,
                        ktype=normalized_ktype,
                        autype=normalized_autype,
                        requested_start=requested_start,
                        requested_end=target.end_date,
                        fetched_count=len(rows),
                        inserted_count=inserted_count,
                        updated_count=updated_count,
                    )
                )
            except Exception as exc:
                items.append(
                    MarketHistorySyncItem(
                        target=target,
                        ktype=normalized_ktype,
                        autype=normalized_autype,
                        requested_start=requested_start,
                        requested_end=target.end_date,
                        error=str(exc),
                    )
                )

    return MarketHistorySyncResult(
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        database_path=db_path,
        items=tuple(items),
    )


def list_latest_market_quotes(
    session: Session,
    *,
    user_id: int,
    database_path: str | Path | None = None,
    include_positions: bool = True,
    include_trades: bool = False,
) -> list[MarketQuoteItem]:
    targets = collect_market_history_targets(
        session,
        user_id=user_id,
        include_positions=include_positions,
        include_trades=include_trades,
    )
    if not targets:
        return []

    db_path = Path(database_path) if database_path is not None else get_market_data_db_path()
    items: list[MarketQuoteItem] = []
    with connect_market_data_db(db_path) as conn:
        for target in targets:
            row = conn.execute(
                """
                SELECT *
                FROM market_quote
                WHERE market = ?
                  AND symbol = ?
                  AND provider = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (target.market, target.symbol, MARKET_DATA_PROVIDER_AKSHARE),
            ).fetchone()
            if not row:
                continue
            items.append(market_quote_item_from_row(row))
    return items


def refresh_market_quotes_from_akshare(
    session: Session,
    *,
    user_id: int,
    database_path: str | Path | None = None,
    include_positions: bool = True,
    include_trades: bool = False,
    limit: int | None = None,
) -> MarketQuoteRefreshResult:
    targets = collect_market_history_targets(
        session,
        user_id=user_id,
        include_positions=include_positions,
        include_trades=include_trades,
    )
    if limit is not None:
        targets = targets[: max(0, int(limit))]

    fetched_at = time.time()
    db_path = Path(database_path) if database_path is not None else get_market_data_db_path()
    try:
        spot_rows = load_akshare_spot_rows()
    except Exception as exc:
        items = tuple(
            MarketQuoteItem(
                provider=MARKET_DATA_PROVIDER_AKSHARE,
                market=target.market,
                symbol=target.symbol,
                provider_code=target.provider_code,
                name=target.name,
                price=None,
                fetched_at=fetched_at,
                error=str(exc),
            )
            for target in targets
        )
        return MarketQuoteRefreshResult(provider=MARKET_DATA_PROVIDER_AKSHARE, database_path=db_path, items=items)

    row_by_symbol = {
        normalize_symbol(str(_first_existing(row, "代码", "code", "symbol"))): row
        for row in spot_rows
        if _first_existing(row, "代码", "code", "symbol")
    }
    items: list[MarketQuoteItem] = []
    for target in targets:
        raw_row = row_by_symbol.get(target.symbol)
        if raw_row is None:
            items.append(
                MarketQuoteItem(
                    provider=MARKET_DATA_PROVIDER_AKSHARE,
                    market=target.market,
                    symbol=target.symbol,
                    provider_code=target.provider_code,
                    name=target.name,
                    price=None,
                    fetched_at=fetched_at,
                    error="AKShare 未返回实时行情",
                )
            )
            continue
        items.append(market_quote_item_from_akshare_spot_row(target, raw_row, fetched_at=fetched_at))

    with connect_market_data_db(db_path) as conn:
        upsert_quote_items(conn, items=items)

    return MarketQuoteRefreshResult(
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        database_path=db_path,
        items=tuple(items),
    )


def load_akshare_spot_rows() -> list[dict[str, Any]]:
    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover - environment guard
        raise RuntimeError("AKShare 未安装或无法导入，请先执行 uv sync。") from exc

    rows: list[dict[str, Any]] = []
    fetchers = (
        ("stock_zh_a_spot_em", getattr(ak, "stock_zh_a_spot_em", None)),
        ("fund_etf_spot_em", getattr(ak, "fund_etf_spot_em", None)),
    )
    errors: list[str] = []
    for name, fetcher in fetchers:
        if not callable(fetcher):
            continue
        try:
            frame = fetcher()
        except Exception as exc:
            if not _looks_like_proxy_error(exc):
                errors.append(f"{name}: {exc}")
                continue
            try:
                with _without_proxy_env():
                    frame = fetcher()
            except Exception as retry_exc:
                errors.append(f"{name}: {retry_exc}")
                continue
        try:
            if hasattr(frame, "to_dict"):
                rows.extend(dict(row) for row in frame.to_dict("records"))
        except Exception as exc:
            errors.append(f"{name}: {exc}")

    if not rows:
        suffix = "；".join(errors) if errors else "未找到可用的 AKShare 实时行情函数"
        raise RuntimeError(f"AKShare 实时行情获取失败：{suffix}")
    return rows


def serialize_sync_result(result: MarketHistorySyncResult) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "database_path": str(result.database_path),
        "target_count": result.target_count,
        "fetched_count": result.fetched_count,
        "inserted_count": result.inserted_count,
        "updated_count": result.updated_count,
        "items": [serialize_sync_item(item) for item in result.items],
    }


def serialize_sync_item(item: MarketHistorySyncItem) -> dict[str, Any]:
    return {
        "target": {
            "market": item.target.market,
            "symbol": item.target.symbol,
            "provider_code": item.target.provider_code,
            "name": item.target.name,
            "sources": list(item.target.sources),
            "first_trade_date": item.target.first_trade_date,
            "start_date": item.target.start_date,
            "end_date": item.target.end_date,
        },
        "ktype": item.ktype,
        "autype": item.autype,
        "requested_start": item.requested_start,
        "requested_end": item.requested_end,
        "fetched_count": item.fetched_count,
        "inserted_count": item.inserted_count,
        "updated_count": item.updated_count,
        "skipped": item.skipped,
        "error": item.error,
    }


def serialize_quote_refresh_result(result: MarketQuoteRefreshResult, *, error: str = "") -> dict[str, Any]:
    return {
        "provider": result.provider,
        "database_path": str(result.database_path),
        "target_count": result.target_count,
        "refreshed_count": result.refreshed_count,
        "error_count": result.error_count,
        "error": error,
        "items": [serialize_quote_item(item) for item in result.items],
    }


def serialize_quote_item(item: MarketQuoteItem) -> dict[str, Any]:
    return {
        "provider": item.provider,
        "market": item.market,
        "symbol": item.symbol,
        "provider_code": item.provider_code,
        "name": item.name,
        "price": item.price,
        "open_price": item.open_price,
        "high_price": item.high_price,
        "low_price": item.low_price,
        "prev_close_price": item.prev_close_price,
        "volume": item.volume,
        "turnover": item.turnover,
        "update_time": item.update_time,
        "fetched_at": item.fetched_at,
        "error": item.error,
    }


def market_quote_item_from_akshare_spot_row(
    target: MarketHistoryTarget,
    row: dict[str, Any],
    *,
    fetched_at: float,
) -> MarketQuoteItem:
    return MarketQuoteItem(
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        market=target.market,
        symbol=target.symbol,
        provider_code=target.provider_code,
        name=str(_first_existing(row, "名称", "name") or target.name or "").strip(),
        price=_float_or_none(_first_existing(row, "最新价", "现价", "last_price", "price")),
        open_price=_float_or_none(_first_existing(row, "今开", "开盘", "open")),
        high_price=_float_or_none(_first_existing(row, "最高", "high")),
        low_price=_float_or_none(_first_existing(row, "最低", "low")),
        prev_close_price=_float_or_none(_first_existing(row, "昨收", "prev_close", "last_close")),
        volume=_float_or_none(_first_existing(row, "成交量", "volume")),
        turnover=_float_or_none(_first_existing(row, "成交额", "turnover", "amount")),
        update_time=str(_first_existing(row, "更新时间", "update_time", "time") or "").strip(),
        fetched_at=fetched_at,
        raw_json=_json_safe_dict(row),
    )


def market_quote_item_from_row(row: sqlite3.Row) -> MarketQuoteItem:
    raw_text = str(row["raw_json"] or "{}")
    try:
        raw_json = json.loads(raw_text)
    except json.JSONDecodeError:
        raw_json = {}
    return MarketQuoteItem(
        provider=str(row["provider"] or ""),
        market=str(row["market"] or ""),
        symbol=str(row["symbol"] or ""),
        provider_code=str(row["provider_code"] or ""),
        name=str(row["name"] or ""),
        price=_float_or_none(row["price"]),
        open_price=_float_or_none(row["open_price"]),
        high_price=_float_or_none(row["high_price"]),
        low_price=_float_or_none(row["low_price"]),
        prev_close_price=_float_or_none(row["prev_close_price"]),
        volume=_float_or_none(row["volume"]),
        turnover=_float_or_none(row["turnover"]),
        update_time=str(row["update_time"] or ""),
        fetched_at=float(row["fetched_at"] or 0),
        raw_json=raw_json if isinstance(raw_json, dict) else {},
    )


def _kline_row_from_akshare_history_row(row: Any) -> dict[str, Any]:
    return {
        "date": row.date,
        "time_key": row.date,
        "open": row.open,
        "close": row.close,
        "high": row.high,
        "low": row.low,
        "volume": row.volume,
        "turnover": row.amount,
        "turnover_rate": row.turnover_rate,
        "change_rate": row.change_percent,
        "raw_symbol": row.symbol,
    }


def _first_existing(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric == numeric else None


def _json_safe_dict(row: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        else:
            safe[str(key)] = str(value)
    return safe


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
