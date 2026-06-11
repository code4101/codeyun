from __future__ import annotations

import csv
from types import SimpleNamespace

from backend.core.stock.akshare_market import AkshareStockHistory, AkshareStockHistoryRow
from backend.core.stock.market_data import connect_market_data_db
from backend.core.stock.qlib_bridge import (
    QlibWatchTarget,
    analyze_qlib_daily_target,
    backtest_qlib_one_lot_score_strategy,
    export_qlib_daily_dataset,
)


def test_export_qlib_daily_dataset_writes_csv_from_akshare(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    _patch_qlib_market_data_cache(monkeypatch, tmp_path / "market-data.sqlite")

    def fake_fetch_akshare_stock_history(**kwargs):
        assert kwargs["market"] == "SZ"
        assert kwargs["symbol"] == "159278"
        assert kwargs["period"] == "daily"
        return AkshareStockHistory(
            provider="akshare",
            market="SZ",
            symbol="159278",
            name="机器人PH",
            period="daily",
            adjust="",
            start_date="20250812",
            end_date="20260611",
            rows=(
                AkshareStockHistoryRow(
                    date="2026-06-11",
                    symbol="159278",
                    open=1.08,
                    close=1.09,
                    high=1.10,
                    low=1.07,
                    volume=12345,
                    amount=1345600,
                    amplitude=None,
                    change_percent=0.92,
                    change_amount=0.01,
                    turnover_rate=1.2,
                ),
            ),
        )

    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.fetch_akshare_stock_history",
        fake_fetch_akshare_stock_history,
    )

    result = export_qlib_daily_dataset(
        targets=(QlibWatchTarget(market="SZ", symbol="159278", name="机器人PH", start_date="2025-08-12"),),
        qlib_repo_path=tmp_path / "qlib",
    )

    assert result.exported_count == 1
    assert "dump_bin.py" in result.dump_command
    assert "--exclude_fields date,symbol" in result.dump_command
    item = result.items[0]
    assert item.qlib_symbol == "sz159278"
    assert item.row_count == 1

    with item.csv_path.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert rows == [
        {
            "date": "2026-06-11",
            "symbol": "SZ159278",
            "open": "1.08",
            "close": "1.09",
            "high": "1.1",
            "low": "1.07",
            "volume": "12345.0",
            "amount": "1345600.0",
            "change": "0.92",
        }
    ]
    cached_rows = _cached_daily_count(tmp_path / "market-data.sqlite", "SZ", "159278")
    assert cached_rows == 1


def test_analyze_qlib_daily_target_returns_factor_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    _patch_qlib_market_data_cache(monkeypatch, tmp_path / "market-data.sqlite")

    history_rows = tuple(
        AkshareStockHistoryRow(
            date=f"2026-04-{index + 1:02d}" if index < 30 else f"2026-05-{index - 29:02d}",
            symbol="159278",
            open=1 + index * 0.001,
            close=1 + index * 0.002,
            high=1.01 + index * 0.002,
            low=0.99 + index * 0.002,
            volume=10000 + index * 100,
            amount=1000000 + index * 10000,
            amplitude=None,
            change_percent=0.2,
            change_amount=None,
            turnover_rate=1.0,
        )
        for index in range(40)
    )

    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.fetch_akshare_stock_history",
        lambda **kwargs: AkshareStockHistory(
            provider="akshare",
            market="SZ",
            symbol="159278",
            name="机器人PH",
            period="daily",
            adjust="",
            start_date="20260401",
            end_date="20260510",
            rows=history_rows,
        ),
    )

    analysis = analyze_qlib_daily_target(
        market="SZ",
        symbol="159278",
        name="机器人PH",
        start_date="2026-04-01",
        refresh=True,
    )

    assert analysis.row_count == 40
    assert analysis.qlib_symbol == "sz159278"
    assert analysis.return_5 is not None
    assert analysis.ma_20 is not None
    assert analysis.score is not None
    assert analysis.signal in {"偏积极", "中性观察", "偏谨慎"}
    assert _cached_daily_count(tmp_path / "market-data.sqlite", "SZ", "159278") == 40


def test_analyze_qlib_daily_target_does_not_rewrite_csv_from_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    _patch_qlib_market_data_cache(monkeypatch, tmp_path / "market-data.sqlite")
    target = QlibWatchTarget(market="SZ", symbol="159278", name="机器人PH", start_date="2026-04-01")
    history_rows = tuple(
        AkshareStockHistoryRow(
            date=f"2026-04-{index + 1:02d}",
            symbol="159278",
            open=1,
            close=1 + index * 0.01,
            high=1 + index * 0.01,
            low=1,
            volume=10000,
            amount=1000000,
            amplitude=None,
            change_percent=0.2,
            change_amount=None,
            turnover_rate=1.0,
        )
        for index in range(25)
    )
    from backend.core.stock.qlib_bridge import _cache_daily_rows

    _cache_daily_rows(target, history_rows)

    analysis = analyze_qlib_daily_target(
        market="SZ",
        symbol="159278",
        name="机器人PH",
        start_date="2026-04-01",
        refresh=False,
    )

    assert analysis.row_count == 25
    assert not (tmp_path / "stock" / "qlib" / "source" / "day" / "sz159278.csv").exists()


def test_backtest_one_lot_score_strategy_allows_multiple_open_lots(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.core.stock.qlib_bridge.get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    _patch_qlib_market_data_cache(monkeypatch, tmp_path / "market-data.sqlite")
    target = QlibWatchTarget(market="HK", symbol="01810", name="小米集团", start_date="1990-01-01")
    history_rows = tuple(
        AkshareStockHistoryRow(
            date=f"2025-01-{index + 1:02d}",
            symbol="01810",
            open=10,
            close=10 + index * 0.1,
            high=10 + index * 0.1,
            low=9.8,
            volume=10000 + index * 2000,
            amount=1000000 + index * 10000,
            amplitude=None,
            change_percent=1.0,
            change_amount=None,
            turnover_rate=1.0,
        )
        for index in range(28)
    )
    from backend.core.stock.qlib_bridge import _cache_daily_rows

    _cache_daily_rows(target, history_rows)

    result = backtest_qlib_one_lot_score_strategy(
        market="HK",
        symbol="01810",
        name="小米集团",
        start_date="2025-01-20",
        end_date="2025-01-28",
        lot_size=200,
        score_threshold=70,
        take_profit_percent=5,
        cost_rate=0.01,
        refresh=False,
    )

    assert result.capital_mode == "unlimited"
    assert result.trade_count >= 2
    assert result.total_invested > 0
    assert result.max_capital_used > 0
    assert any(point.action and "买入" in point.action for point in result.points)
    assert all(trade.shares == 200 for trade in result.trades)


def _patch_qlib_market_data_cache(monkeypatch, database_path):
    def connect_test_market_data_db(path=None):
        return connect_market_data_db(database_path if path is None else path)

    monkeypatch.setattr("backend.core.stock.qlib_bridge.connect_market_data_db", connect_test_market_data_db)


def _cached_daily_count(database_path, market: str, symbol: str) -> int:
    with connect_market_data_db(database_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS row_count
            FROM market_kline
            WHERE provider = 'akshare'
              AND market = ?
              AND symbol = ?
              AND ktype = 'daily'
              AND autype = 'none'
            """,
            (market, symbol),
        ).fetchone()
    return int(row["row_count"])
