from __future__ import annotations

import sqlite3

from sqlmodel import SQLModel, Session, create_engine

from backend.core.stock.market_data import (
    MarketHistoryTarget,
    MarketQuoteItem,
    MARKET_DATA_PROVIDER_EASTMONEY_PUBLIC,
    collect_market_history_targets,
    ensure_market_data_schema,
    market_quote_item_from_eastmoney_public_snapshot,
    normalize_market_code,
    to_eastmoney_secid,
    to_futu_code,
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
        ("HK", "01810", "HK.01810"),
        ("SZ", "159278", "SZ.159278"),
    ]
    hk_target, robot_target = targets
    assert hk_target.start_date == "2026-04-19"
    assert hk_target.sources == ("position:hk_position", "position:normal_position")
    assert robot_target.first_trade_date == "2026-02-24"
    assert robot_target.start_date == "2026-02-24"


def test_market_code_normalization_for_futu() -> None:
    assert normalize_market_code("HK", "1810") == ("HK", "01810")
    assert normalize_market_code("", "600050") == ("SH", "600050")
    assert normalize_market_code("", "159278") == ("SZ", "159278")
    assert to_futu_code("SH", "510980") == "SH.510980"
    assert to_eastmoney_secid("SZ", "159278") == "0.159278"
    assert to_eastmoney_secid("HK", "1810") == "116.01810"


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
        provider="futu",
        target=target,
        ktype="1m",
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
        provider="futu",
        target=target,
        ktype="1m",
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
        provider="futu",
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
        provider="futu",
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
        "SELECT price, update_time, fetched_at FROM market_quote WHERE provider = 'futu' AND symbol = '01810'"
    ).fetchone()
    assert dict(row) == {
        "price": 36.25,
        "update_time": "2026-05-20 10:01:00",
        "fetched_at": 160.0,
    }


def test_market_quote_item_from_eastmoney_public_snapshot_scales_price() -> None:
    target = MarketHistoryTarget(
        market="SZ",
        symbol="159278",
        provider_code="SZ.159278",
        name="机器人PH",
        sources=("position:normal_position",),
        first_trade_date="",
        start_date="2026-05-01",
        end_date="2026-05-20",
    )

    item = market_quote_item_from_eastmoney_public_snapshot(
        target,
        {
            "f43": 1188,
            "f44": 1195,
            "f45": 1178,
            "f46": 1190,
            "f47": 992796,
            "f48": 117647964.727,
            "f57": "159278",
            "f58": "机器人ETF鹏华",
            "f59": 3,
            "f60": 1203,
            "f86": 1779262491,
        },
        fetched_at=200,
    )

    assert item.provider == MARKET_DATA_PROVIDER_EASTMONEY_PUBLIC
    assert item.provider_code == "0.159278"
    assert item.name == "机器人ETF鹏华"
    assert item.price == 1.188
    assert item.open_price == 1.19
    assert item.prev_close_price == 1.203
    assert item.update_time.startswith("2026-")
