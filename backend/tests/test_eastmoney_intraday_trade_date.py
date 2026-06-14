import sqlite3
from contextlib import contextmanager

from backend.api import eastmoney
from backend.core.stock.akshare_market import AkshareEtfIntraday, AkshareEtfIntradayRow
from backend.core.stock.market_data import ensure_market_data_schema


def _intraday_row(time: str = "2026-06-10 09:31:00") -> AkshareEtfIntradayRow:
    return AkshareEtfIntradayRow(
        time=time,
        symbol="03896",
        open=1.0,
        close=1.1,
        high=1.2,
        low=0.9,
        volume=100,
        amount=110,
        average_price=1.05,
    )


def test_latest_persisted_history_date_ignores_non_date_time_keys(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE market_kline (
            provider TEXT,
            market TEXT,
            symbol TEXT,
            ktype TEXT,
            time_key TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO market_kline (provider, market, symbol, ktype, time_key) VALUES (?, ?, ?, ?, ?)",
        [
            ("akshare", "HK", "03896", "daily", "2026-06-10"),
            ("akshare", "HK", "03896", "daily", "2026-06-12"),
            ("akshare", "HK", "03896", "daily", "annotation"),
        ],
    )

    @contextmanager
    def fake_connect():
        yield conn

    monkeypatch.setattr(eastmoney, "connect_market_data_db", fake_connect)

    assert eastmoney.read_latest_persisted_history_date(market="HK", symbol="03896") == "2026-06-12"


def test_intraday_empty_state_uses_latest_persisted_daily_date(monkeypatch):
    monkeypatch.setattr(eastmoney, "read_persisted_akshare_intraday", lambda **_kwargs: None)
    monkeypatch.setattr(eastmoney, "read_latest_persisted_history_date", lambda **_kwargs: "2026-06-12")

    data = eastmoney.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
        refresh=False,
    )

    assert data["provider"] == "market-data"
    assert data["trade_date"] == "2026-06-12"
    assert data["target_trade_date"] == "2026-06-12"
    assert data["display_trade_date"] == "2026-06-12"
    assert data["items"] == []
    assert data["error"] == "本地暂无目标交易日 2026-06-12 分时持久化数据；可点补下载尝试拉取并落库"


def test_intraday_resets_when_daily_date_is_newer_than_persisted_intraday(monkeypatch):
    cached = AkshareEtfIntraday(
        provider="market-data",
        market="HK",
        symbol="03896",
        name="金山云",
        period="1",
        trade_date="2026-06-10",
        rows=(_intraday_row(),),
    )
    monkeypatch.setattr(eastmoney, "read_persisted_akshare_intraday", lambda **_kwargs: cached)
    monkeypatch.setattr(eastmoney, "read_latest_persisted_history_date", lambda **_kwargs: "2026-06-12")

    data = eastmoney.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
        refresh=False,
    )

    assert data["provider"] == "market-data"
    assert data["trade_date"] == "2026-06-10"
    assert data["target_trade_date"] == "2026-06-12"
    assert data["display_trade_date"] == "2026-06-10"
    assert len(data["items"]) == 1
    assert "2026-06-12" in data["error"]
    assert "2026-06-10" in data["error"]


def test_intraday_refresh_targets_latest_persisted_daily_date(monkeypatch):
    requested_dates: list[str | None] = []

    def fake_fetch(**kwargs):
        requested_dates.append(kwargs["trade_date"])
        raise RuntimeError("network failed")

    monkeypatch.setattr(eastmoney, "read_persisted_akshare_intraday", lambda **_kwargs: None)
    monkeypatch.setattr(eastmoney, "read_latest_persisted_history_date", lambda **_kwargs: "2026-06-12")
    monkeypatch.setattr(eastmoney, "fetch_akshare_etf_intraday", fake_fetch)

    data = eastmoney.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
        refresh=True,
    )

    assert requested_dates == ["2026-06-12"]
    assert data["provider"] == "akshare-error"
    assert data["trade_date"] == "2026-06-12"
    assert data["target_trade_date"] == "2026-06-12"
    assert data["display_trade_date"] == "2026-06-12"
    assert data["error"] == "本地暂无目标交易日 2026-06-12 分时持久化数据；补下载失败：network failed"


def test_intraday_refresh_failure_falls_back_to_latest_persisted_intraday(monkeypatch):
    requested_dates: list[str | None] = []
    cached = AkshareEtfIntraday(
        provider="market-data",
        market="HK",
        symbol="03896",
        name="金山云",
        period="1",
        trade_date="2026-06-10",
        rows=(_intraday_row(),),
    )

    def fake_read(**kwargs):
        return cached if kwargs.get("trade_date") is None else None

    def fake_fetch(**kwargs):
        requested_dates.append(kwargs["trade_date"])
        raise RuntimeError("network failed")

    monkeypatch.setattr(eastmoney, "read_persisted_akshare_intraday", fake_read)
    monkeypatch.setattr(eastmoney, "read_latest_persisted_history_date", lambda **_kwargs: "2026-06-12")
    monkeypatch.setattr(eastmoney, "fetch_akshare_etf_intraday", fake_fetch)

    data = eastmoney.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
        refresh=True,
    )

    assert requested_dates == ["2026-06-12"]
    assert data["provider"] == "market-data"
    assert data["trade_date"] == "2026-06-10"
    assert data["target_trade_date"] == "2026-06-12"
    assert data["display_trade_date"] == "2026-06-10"
    assert len(data["items"]) == 1
    assert "补下载目标交易日 2026-06-12 分时失败" in data["error"]


def test_intraday_refresh_marks_empty_akshare_response(monkeypatch):
    monkeypatch.setattr(eastmoney, "read_persisted_akshare_intraday", lambda **_kwargs: None)
    monkeypatch.setattr(eastmoney, "read_latest_persisted_history_date", lambda **_kwargs: "2026-06-12")
    monkeypatch.setattr(
        eastmoney,
        "fetch_akshare_etf_intraday",
        lambda **_kwargs: AkshareEtfIntraday(
            provider="akshare",
            market="HK",
            symbol="03896",
            name="金山云",
            period="1",
            trade_date="2026-06-12",
            rows=(),
        ),
    )

    data = eastmoney.get_akshare_market_intraday(
        market="HK",
        symbol="03896",
        name="金山云",
        trade_date=None,
        period="1",
        day_count=1,
        refresh=True,
    )

    assert data["provider"] == "akshare-empty"
    assert data["trade_date"] == "2026-06-12"
    assert data["target_trade_date"] == "2026-06-12"
    assert data["display_trade_date"] == "2026-06-12"
    assert data["items"] == []
    assert "2026-06-12" in data["error"]


def test_persist_intraday_keeps_each_row_trade_date(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_market_data_schema(conn)

    @contextmanager
    def fake_connect():
        yield conn

    monkeypatch.setattr(eastmoney, "connect_market_data_db", fake_connect)

    eastmoney.persist_akshare_intraday(
        AkshareEtfIntraday(
            provider="akshare",
            market="HK",
            symbol="03896",
            name="金山云",
            period="1",
            trade_date="2026-06-12",
            rows=(
                _intraday_row("2026-06-10 09:31:00"),
                _intraday_row("2026-06-12 09:31:00"),
            ),
        )
    )

    rows = conn.execute(
        """
        SELECT trade_date, COUNT(*) AS count
        FROM market_intraday
        WHERE provider = 'akshare'
          AND market = 'HK'
          AND symbol = '03896'
          AND period = '1'
        GROUP BY trade_date
        ORDER BY trade_date
        """
    ).fetchall()

    assert [tuple(row) for row in rows] == [("2026-06-10", 1), ("2026-06-12", 1)]
