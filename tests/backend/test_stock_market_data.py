from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import SQLModel, Session, create_engine

from backend.api import eastmoney as eastmoney_api
from backend.core.stock.akshare_market import (
    AkshareStockHistory,
    AkshareStockHistoryRow,
    fetch_akshare_etf_intraday,
    fetch_akshare_stock_history,
    serialize_akshare_etf_intraday,
)
from backend.core.stock.market_data import (
    MARKET_DATA_PROVIDER_AKSHARE,
    MarketHistoryTarget,
    MarketQuoteItem,
    collect_market_history_targets,
    connect_market_data_db,
    ensure_market_data_schema,
    market_quote_item_from_akshare_spot_row,
    normalize_market_code,
    to_akshare_code,
    upsert_kline_rows,
    upsert_quote_items,
)
from backend.models import EastmoneyPositionSnapshot, EastmoneyTradeRecord, User


def _build_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_collect_market_history_targets_dedupes_latest_positions_and_trade_start() -> None:
    with _build_session() as session:
        user = User(username="stock-user", email="stock@example.com", hashed_password="pw")
        session.add(user)
        session.commit()
        session.refresh(user)

        session.add_all(
            [
                EastmoneyPositionSnapshot(
                    user_id=user.id,
                    sync_run_id="run-old",
                    source="hk_position",
                    market="HK",
                    captured_at=100,
                    security_code="01810",
                    security_name="小米集团",
                    quantity="100",
                ),
                EastmoneyPositionSnapshot(
                    user_id=user.id,
                    sync_run_id="run-new",
                    source="normal_position",
                    market="HK",
                    captured_at=200,
                    security_code="1810",
                    security_name="小米集团",
                    quantity="100",
                ),
                EastmoneyPositionSnapshot(
                    user_id=user.id,
                    sync_run_id="run-new",
                    source="hk_position",
                    market="HK",
                    captured_at=200,
                    security_code="01810",
                    security_name="小米集团",
                    quantity="100",
                ),
                EastmoneyTradeRecord(
                    user_id=user.id,
                    source_key="trade-1",
                    market="SZ",
                    security_code="159278",
                    security_name="机器人PH",
                    trade_date="2026-02-24",
                    source="normal_history_deal",
                ),
                EastmoneyTradeRecord(
                    user_id=user.id,
                    source_key="trade-2",
                    market="SZ",
                    security_code="159278",
                    security_name="机器人PH",
                    trade_date="2026-04-08",
                    source="normal_history_deal",
                ),
            ]
        )
        session.commit()

        targets = collect_market_history_targets(
            session,
            user_id=user.id,
            end_date="2026-05-19",
            lookback_days=30,
        )

    assert [(target.market, target.symbol, target.provider_code) for target in targets] == [
        ("HK", "01810", "01810"),
        ("SZ", "159278", "159278"),
    ]
    hk_target, robot_target = targets
    assert hk_target.start_date == "2026-04-19"
    assert hk_target.sources == ("position:hk_position", "position:normal_position")
    assert robot_target.first_trade_date == "2026-02-24"
    assert robot_target.start_date == "2026-02-24"


def test_market_code_normalization_for_akshare() -> None:
    assert normalize_market_code("HK", "1810") == ("HK", "01810")
    assert normalize_market_code("", "600050") == ("SH", "600050")
    assert normalize_market_code("", "159278") == ("SZ", "159278")
    assert to_akshare_code("SH", "510980") == "510980"
    assert to_akshare_code("SZ", "159278") == "159278"
    assert to_akshare_code("HK", "1810") == "01810"


def test_upsert_kline_rows_updates_existing_records() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_market_data_schema(conn)
    target = MarketHistoryTarget(
        market="HK",
        symbol="01810",
        provider_code="HK.01810",
        name="小米集团",
        sources=("position:hk_position",),
        first_trade_date="",
        start_date="2026-05-01",
        end_date="2026-05-19",
    )

    inserted, updated = upsert_kline_rows(
        conn,
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        target=target,
        ktype="daily",
        autype="none",
        rows=[
            {
                "time_key": "2026-05-19 09:30:00",
                "open": 10,
                "close": 10.5,
                "high": 10.8,
                "low": 9.9,
                "volume": 1000,
            }
        ],
        provisional_date="2026-05-19",
    )
    assert (inserted, updated) == (1, 0)

    inserted, updated = upsert_kline_rows(
        conn,
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        target=target,
        ktype="daily",
        autype="none",
        rows=[
            {
                "time_key": "2026-05-19 09:30:00",
                "open": 10,
                "close": 10.6,
                "high": 10.9,
                "low": 9.9,
                "volume": 1200,
            }
        ],
        provisional_date="2026-05-19",
    )
    assert (inserted, updated) == (0, 1)
    row = conn.execute("SELECT close, volume, provisional FROM market_kline").fetchone()
    assert dict(row) == {"close": 10.6, "volume": 1200.0, "provisional": 1}


def test_upsert_quote_items_updates_latest_snapshot() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_market_data_schema(conn)

    first_item = MarketQuoteItem(
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        market="HK",
        symbol="01810",
        provider_code="HK.01810",
        name="小米集团",
        price=35.5,
        update_time="2026-05-20 10:00:00",
        fetched_at=100,
    )
    inserted, updated = upsert_quote_items(conn, items=[first_item])
    assert (inserted, updated) == (1, 0)

    second_item = MarketQuoteItem(
        provider=MARKET_DATA_PROVIDER_AKSHARE,
        market="HK",
        symbol="01810",
        provider_code="HK.01810",
        name="小米集团",
        price=36.25,
        update_time="2026-05-20 10:01:00",
        fetched_at=160,
    )
    inserted, updated = upsert_quote_items(conn, items=[second_item])
    assert (inserted, updated) == (0, 1)

    row = conn.execute(
        "SELECT price, update_time, fetched_at FROM market_quote WHERE provider = 'akshare' AND symbol = '01810'"
    ).fetchone()
    assert dict(row) == {
        "price": 36.25,
        "update_time": "2026-05-20 10:01:00",
        "fetched_at": 160.0,
    }


def test_market_quote_item_from_akshare_spot_row() -> None:
    target = MarketHistoryTarget(
        market="SZ",
        symbol="159278",
        provider_code="159278",
        name="机器人PH",
        sources=("position:normal_position",),
        first_trade_date="",
        start_date="2026-05-01",
        end_date="2026-05-20",
    )

    item = market_quote_item_from_akshare_spot_row(
        target,
        {
            "代码": "159278",
            "名称": "机器人ETF鹏华",
            "最新价": 1.188,
            "今开": 1.19,
            "最高": 1.195,
            "最低": 1.178,
            "昨收": 1.203,
            "成交量": 992796,
            "成交额": 117647964.727,
            "更新时间": "2026-05-20 10:14:51",
        },
        fetched_at=200,
    )

    assert item.provider == MARKET_DATA_PROVIDER_AKSHARE
    assert item.provider_code == "159278"
    assert item.name == "机器人ETF鹏华"
    assert item.price == 1.188
    assert item.open_price == 1.19
    assert item.prev_close_price == 1.203
    assert item.update_time == "2026-05-20 10:14:51"


def test_fetch_akshare_etf_intraday_uses_one_minute_rows(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "时间": "2026-06-11 09:31:00",
                    "开盘": 1.08,
                    "收盘": 1.083,
                    "最高": 1.085,
                    "最低": 1.079,
                    "成交量": 3022453,
                    "成交额": 3270000,
                    "均价": 1.082,
                }
            ]

    def fake_fund_etf_hist_min_em(**kwargs):
        calls.update(kwargs)
        return FakeFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_etf_hist_min_em=fake_fund_etf_hist_min_em),
    )

    intraday = fetch_akshare_etf_intraday(
        symbol="SZ.159278",
        name="机器人PH",
        trade_date="2026-06-11",
        period="1",
    )
    payload = serialize_akshare_etf_intraday(intraday)

    assert calls == {
        "symbol": "159278",
        "start_date": "2026-06-11 09:30:00",
        "end_date": "2026-06-11 15:00:00",
        "period": "1",
        "adjust": "",
    }
    assert payload["provider"] == "akshare"
    assert payload["market"] == "SZ"
    assert payload["period"] == "1"
    assert payload["trade_date"] == "20260611"
    assert payload["items"] == [
        {
            "time": "2026-06-11 09:31:00",
            "symbol": "159278",
            "open": 1.08,
            "close": 1.083,
            "high": 1.085,
            "low": 1.079,
            "volume": 3022453.0,
            "amount": 3270000.0,
            "average_price": 1.082,
        }
    ]


def test_fetch_akshare_hk_intraday_uses_hk_minute_rows(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "时间": "2026-06-11 09:31:00",
                    "开盘": 26.22,
                    "收盘": 26.28,
                    "最高": 26.32,
                    "最低": 26.18,
                    "成交量": 1149400,
                    "成交额": 30176852,
                    "最新价": 26.2353,
                }
            ]

    def fake_stock_hk_hist_min_em(**kwargs):
        calls.update(kwargs)
        return FakeFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_hk_hist_min_em=fake_stock_hk_hist_min_em),
    )

    intraday = fetch_akshare_etf_intraday(
        market="HK",
        symbol="1810",
        name="小米集团",
        trade_date="2026-06-11",
        period="1",
    )
    payload = serialize_akshare_etf_intraday(intraday)

    assert calls == {
        "symbol": "01810",
        "start_date": "2026-06-11 09:30:00",
        "end_date": "2026-06-11 16:00:00",
        "period": "1",
        "adjust": "",
    }
    assert payload["market"] == "HK"
    assert payload["symbol"] == "01810"
    assert payload["items"][0]["average_price"] == 26.2353


def test_fetch_akshare_hk_history_falls_back_to_sina_daily(monkeypatch) -> None:
    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "date": "2026-06-09",
                    "open": 5.93,
                    "high": 6.13,
                    "low": 5.75,
                    "close": 5.94,
                    "volume": 79130307,
                    "amount": 470587674,
                },
                {
                    "date": "2026-06-10",
                    "open": 5.89,
                    "high": 6.05,
                    "low": 5.70,
                    "close": 5.87,
                    "volume": 71410606,
                    "amount": 418881412,
                },
            ]

    def fake_stock_hk_hist(**_kwargs):
        raise RuntimeError("eastmoney unavailable")

    def fake_stock_hk_daily(**kwargs):
        assert kwargs == {"symbol": "03896", "adjust": ""}
        return FakeFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_hk_hist=fake_stock_hk_hist, stock_hk_daily=fake_stock_hk_daily),
    )

    history = fetch_akshare_stock_history(
        market="HK",
        symbol="03896",
        name="金山云",
        period="daily",
        start_date="2026-06-01",
        end_date="2026-06-11",
    )

    assert history.market == "HK"
    assert history.symbol == "03896"
    assert len(history.rows) == 2
    assert history.rows[0].date == "2026-06-09"
    assert history.rows[0].close == 5.94
    assert history.rows[0].amount == 470587674.0


def test_fetch_akshare_hk_monthly_history_falls_back_to_sina_and_aggregates(monkeypatch) -> None:
    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {"date": "2026-05-29", "open": 5.0, "high": 5.8, "low": 4.9, "close": 5.6, "volume": 100, "amount": 560},
                {"date": "2026-06-09", "open": 5.7, "high": 6.2, "low": 5.6, "close": 6.0, "volume": 200, "amount": 1200},
                {"date": "2026-06-10", "open": 5.9, "high": 6.1, "low": 5.7, "close": 5.8, "volume": 300, "amount": 1740},
            ]

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_hk_hist=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("eastmoney unavailable")),
            stock_hk_daily=lambda **_kwargs: FakeFrame(),
        ),
    )

    history = fetch_akshare_stock_history(
        market="HK",
        symbol="03896",
        name="金山云",
        period="monthly",
        start_date="2026-05-01",
        end_date="2026-06-30",
    )

    assert [row.date for row in history.rows] == ["2026-05-29", "2026-06-10"]
    june = history.rows[1]
    assert june.open == 5.7
    assert june.close == 5.8
    assert june.high == 6.2
    assert june.low == 5.6
    assert june.volume == 500.0
    assert june.amount == 2940.0


def test_fetch_akshare_hk_history_normalizes_zero_ohlc_from_sina(monkeypatch) -> None:
    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {"date": "2026-01-05", "open": 0, "high": 0, "low": 0, "close": 0.54, "volume": 100, "amount": 54},
                {"date": "2026-01-06", "open": 0.56, "high": 0, "low": 0, "close": 0.57, "volume": 200, "amount": 114},
            ]

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_hk_hist=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("eastmoney unavailable")),
            stock_hk_daily=lambda **_kwargs: FakeFrame(),
        ),
    )

    history = fetch_akshare_stock_history(
        market="HK",
        symbol="00253",
        name="顺豪控股",
        period="monthly",
        start_date="2026-01-01",
        end_date="2026-01-31",
    )

    assert len(history.rows) == 1
    january = history.rows[0]
    assert january.open == 0.54
    assert january.close == 0.57
    assert january.high == 0.57
    assert january.low == 0.54


def test_fetch_akshare_intraday_aggregates_120_minute_rows(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "时间": "2026-06-11 09:30:00",
                    "开盘": 1.00,
                    "收盘": 1.05,
                    "最高": 1.06,
                    "最低": 0.99,
                    "成交量": 100,
                    "成交额": 105,
                    "均价": 1.03,
                },
                {
                    "时间": "2026-06-11 10:30:00",
                    "开盘": 1.05,
                    "收盘": 1.08,
                    "最高": 1.09,
                    "最低": 1.04,
                    "成交量": 200,
                    "成交额": 216,
                    "均价": 1.06,
                },
            ]

    def fake_fund_etf_hist_min_em(**kwargs):
        calls.update(kwargs)
        return FakeFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_etf_hist_min_em=fake_fund_etf_hist_min_em),
    )

    intraday = fetch_akshare_etf_intraday(
        symbol="159278",
        trade_date="2026-06-11",
        period="120",
    )

    assert calls["period"] == "60"
    assert intraday.period == "120"
    assert len(intraday.rows) == 1
    assert intraday.rows[0].open == 1.0
    assert intraday.rows[0].close == 1.08
    assert intraday.rows[0].high == 1.09
    assert intraday.rows[0].low == 0.99
    assert intraday.rows[0].volume == 300.0


def test_fetch_akshare_history_aggregates_quarterly_rows(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeFrame:
        def to_dict(self, orient: str):
            assert orient == "records"
            return [
                {
                    "日期": "2026-01-31",
                    "开盘": 10,
                    "收盘": 11,
                    "最高": 12,
                    "最低": 9,
                    "成交量": 100,
                    "成交额": 1000,
                    "涨跌幅": 10,
                    "涨跌额": 1,
                    "换手率": 1,
                },
                {
                    "日期": "2026-02-28",
                    "开盘": 11,
                    "收盘": 13,
                    "最高": 14,
                    "最低": 10,
                    "成交量": 200,
                    "成交额": 2600,
                    "涨跌幅": 18.18,
                    "涨跌额": 2,
                    "换手率": 2,
                },
            ]

    def fake_fund_etf_hist_em(**kwargs):
        calls.update(kwargs)
        return FakeFrame()

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(fund_etf_hist_em=fake_fund_etf_hist_em),
    )

    history = fetch_akshare_stock_history(
        symbol="159278",
        period="quarterly",
        start_date="2026-01-01",
        end_date="2026-03-31",
    )

    assert calls["period"] == "monthly"
    assert history.period == "quarterly"
    assert len(history.rows) == 1
    row = history.rows[0]
    assert row.date == "2026-Q1"
    assert row.open == 10
    assert row.close == 13
    assert row.high == 14
    assert row.low == 9
    assert row.volume == 300.0
    assert row.amount == 3600.0


def test_akshare_history_cache_roundtrip_uses_market_data_sqlite(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "market-data.sqlite"
    _patch_eastmoney_api_market_data_cache(monkeypatch, database_path)
    history = AkshareStockHistory(
        provider="akshare",
        market="SZ",
        symbol="159278",
        name="机器人PH",
        period="weekly",
        adjust="",
        start_date="20260224",
        end_date="20260611",
        rows=(
            AkshareStockHistoryRow(
                date="2026-02-27",
                symbol="159278",
                open=1.12,
                close=1.08,
                high=1.14,
                low=1.02,
                volume=123456,
                amount=135000.5,
                amplitude=8.3,
                change_percent=-3.1,
                change_amount=-0.03,
                turnover_rate=1.2,
            ),
        ),
    )

    eastmoney_api.cache_akshare_history(history)
    cached = eastmoney_api.read_cached_akshare_history(
        market="SZ",
        symbol="SZ.159278",
        name="机器人PH",
        period="week",
        start_date="2026-02-24",
        end_date="2026-06-11",
        adjust="",
    )

    assert cached is not None
    assert cached.provider == "akshare-cache"
    assert cached.symbol == "159278"
    assert cached.period == "weekly"
    assert cached.adjust == ""
    assert len(cached.rows) == 1
    assert cached.rows[0].date == "2026-02-27"
    assert cached.rows[0].close == 1.08
    assert cached.rows[0].amount == 135000.5


def test_akshare_history_cache_read_normalizes_zero_ohlc(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "market-data.sqlite"
    _patch_eastmoney_api_market_data_cache(monkeypatch, database_path)
    history = AkshareStockHistory(
        provider="akshare",
        market="HK",
        symbol="00253",
        name="顺豪控股",
        period="monthly",
        adjust="",
        start_date="20260101",
        end_date="20260131",
        rows=(
            AkshareStockHistoryRow(
                date="2026-01-30",
                symbol="00253",
                open=0,
                close=0.65,
                high=0,
                low=0,
                volume=220000,
                amount=143000,
                amplitude=None,
                change_percent=None,
                change_amount=None,
                turnover_rate=None,
            ),
        ),
    )

    eastmoney_api.cache_akshare_history(history)
    cached = eastmoney_api.read_cached_akshare_history(
        market="HK",
        symbol="00253",
        name="顺豪控股",
        period="monthly",
        start_date="2026-01-01",
        end_date="2026-01-31",
        adjust="",
    )

    assert cached is not None
    assert cached.rows[0].open == 0.65
    assert cached.rows[0].high == 0.65
    assert cached.rows[0].low == 0.65


def test_akshare_history_endpoint_falls_back_to_cache(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "market-data.sqlite"
    _patch_eastmoney_api_market_data_cache(monkeypatch, database_path)
    monkeypatch.setattr(
        eastmoney_api,
        "fetch_akshare_stock_history",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    eastmoney_api.cache_akshare_history(
        AkshareStockHistory(
            provider="akshare",
            market="SZ",
            symbol="159278",
            name="机器人PH",
            period="daily",
            adjust="",
            start_date="20260224",
            end_date="20260225",
            rows=(
                AkshareStockHistoryRow(
                    date="2026-02-24",
                    symbol="159278",
                    open=1.15,
                    close=1.16,
                    high=1.17,
                    low=1.14,
                    volume=8600,
                    amount=9959,
                    amplitude=2.61,
                    change_percent=0.87,
                    change_amount=0.01,
                    turnover_rate=0.5,
                ),
            ),
        )
    )

    payload = eastmoney_api.get_akshare_market_history(
        market="SZ",
        symbol="SZ.159278",
        name="机器人PH",
        period="daily",
        start_date="2026-02-24",
        end_date="2026-02-25",
        adjust="",
    )

    assert payload["provider"] == "akshare-cache"
    assert payload["symbol"] == "159278"
    assert payload["items"] == [
        {
            "date": "2026-02-24",
            "symbol": "159278",
            "open": 1.15,
            "close": 1.16,
            "high": 1.17,
            "low": 1.14,
            "volume": 8600.0,
            "amount": 9959.0,
            "amplitude": None,
            "change_percent": 0.87,
            "change_amount": None,
            "turnover_rate": 0.5,
        }
    ]


def test_akshare_history_endpoint_prefers_cache_without_refresh(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "market-data.sqlite"
    _patch_eastmoney_api_market_data_cache(monkeypatch, database_path)

    def fail_if_called(**_kwargs):
        raise AssertionError("AKShare should not be called when cache is available and refresh is false")

    monkeypatch.setattr(eastmoney_api, "fetch_akshare_stock_history", fail_if_called)
    eastmoney_api.cache_akshare_history(
        AkshareStockHistory(
            provider="akshare",
            market="SZ",
            symbol="159278",
            name="机器人PH",
            period="monthly",
            adjust="",
            start_date="20260201",
            end_date="20260228",
            rows=(
                AkshareStockHistoryRow(
                    date="2026-02-28",
                    symbol="159278",
                    open=1.10,
                    close=1.12,
                    high=1.16,
                    low=1.07,
                    volume=9850000,
                    amount=1098000000,
                    amplitude=None,
                    change_percent=0.54,
                    change_amount=None,
                    turnover_rate=71.35,
                ),
            ),
        )
    )

    payload = eastmoney_api.get_akshare_market_history(
        market="SZ",
        symbol="159278",
        name="机器人PH",
        period="monthly",
        start_date="1990-01-01",
        end_date=None,
        adjust="",
        refresh=False,
    )

    assert payload["provider"] == "akshare-cache"
    assert payload["items"][0]["date"] == "2026-02-28"


def test_akshare_history_endpoint_returns_empty_payload_when_cache_missing(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "market-data.sqlite"
    _patch_eastmoney_api_market_data_cache(monkeypatch, database_path)
    monkeypatch.setattr(
        eastmoney_api,
        "fetch_akshare_stock_history",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    payload = eastmoney_api.get_akshare_market_history(
        market="HK",
        symbol="03896",
        name="金山云",
        period="monthly",
        start_date="1990-01-01",
        end_date=None,
        adjust="",
    )

    assert payload["provider"] == "akshare-error"
    assert payload["market"] == "HK"
    assert payload["symbol"] == "03896"
    assert payload["items"] == []
    assert "network down" in payload["error"]


def test_akshare_intraday_endpoint_returns_empty_payload_on_fetch_error(monkeypatch) -> None:
    monkeypatch.setattr(
        eastmoney_api,
        "fetch_akshare_etf_intraday",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )

    payload = eastmoney_api.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
    )

    assert payload["provider"] == "akshare-error"
    assert payload["market"] == "HK"
    assert payload["symbol"] == "03896"
    assert payload["items"] == []
    assert "network down" in payload["error"]


def _patch_eastmoney_api_market_data_cache(monkeypatch, database_path: Path) -> None:
    def connect_test_market_data_db(path: str | Path | None = None):
        return connect_market_data_db(database_path if path is None else path)

    monkeypatch.setattr(eastmoney_api, "connect_market_data_db", connect_test_market_data_db)
