import time

import pytest
from fastapi import HTTPException

from backend.api import eastmoney
from backend.api.eastmoney import (
    EastmoneyCalculatorPayload,
    EastmoneyCalculatorWorkspaceRequest,
    get_eastmoney_calculator_workspace,
    sync_eastmoney_calculator_market_quotes,
    save_eastmoney_calculator_workspace,
)
from backend.models import EastmoneyTradeRecord, User


def _user(session) -> User:
    user = User(
        username="calculator-user",
        email="calculator@example.com",
        hashed_password="pw",
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_calculator_workspace_exposes_signed_history_and_persists(session):
    user = _user(session)
    session.add_all([
        EastmoneyTradeRecord(
            user_id=user.id,
            source_key="buy-1",
            market="SZ",
            trade_date="2026-02-24",
            trade_time="09:30:00",
            security_code="159278",
            security_name="机器人PH",
            direction="买入",
            quantity="8600",
            price="1.158",
        ),
        EastmoneyTradeRecord(
            user_id=user.id,
            source_key="sell-1",
            market="SZ",
            trade_date="2026-05-18",
            trade_time="13:04:08",
            security_code="159278",
            security_name="机器人PH",
            direction="卖出",
            quantity="5000",
            price="1.190",
        ),
    ])
    session.commit()

    initial = get_eastmoney_calculator_workspace(session=session, current_user=user)
    history = initial["history_by_target"]["SZ.159278"]
    assert [(row["time"], row["quantity"]) for row in history] == [
        ("2026-05-18T13:04:08", "-5000"),
        ("2026-02-24T09:30:00", "8600"),
    ]

    saved = save_eastmoney_calculator_workspace(
        EastmoneyCalculatorWorkspaceRequest(items=[
            EastmoneyCalculatorPayload(
                id="robot",
                market="SZ",
                symbol="159278",
                name="机器人PH",
                base_price="1.180",
                trades=[
                    *history,
                    {
                        "id": "legacy-duplicate",
                        "time": "2026-05-18T13:04:08",
                        "price": "1.190",
                        "quantity": "-5000",
                        "source_record_id": "",
                    },
                ],
            ),
        ]),
        session=session,
        current_user=user,
    )
    assert saved["items"][0]["base_price"] == "1.180"
    assert saved["items"][0]["trades"][0]["quantity"] == "-5000"
    assert len(saved["items"][0]["trades"]) == 2


def test_calculator_workspace_rejects_duplicate_stock(session):
    user = _user(session)
    duplicate = EastmoneyCalculatorPayload(
        id="one",
        market="HK",
        symbol="01810",
        name="小米集团",
        base_price="26.200",
    )
    with pytest.raises(HTTPException, match="同一股票不能重复添加"):
        save_eastmoney_calculator_workspace(
            EastmoneyCalculatorWorkspaceRequest(items=[
                duplicate,
                duplicate.model_copy(update={"id": "two"}),
            ]),
            session=session,
            current_user=user,
        )


def test_calculator_quote_sync_reuses_one_minute_database_cache(session, monkeypatch, tmp_path):
    user = _user(session)
    save_eastmoney_calculator_workspace(
        EastmoneyCalculatorWorkspaceRequest(items=[
            EastmoneyCalculatorPayload(
                id="xiaomi",
                market="HK",
                symbol="01810",
                name="小米集团",
                base_price="26.200",
            ),
        ]),
        session=session,
        current_user=user,
    )
    captured = []

    def fake_fetch(target):
        captured.append(target)
        from backend.core.stock.market_data import MarketQuoteItem

        return MarketQuoteItem(**{
            "provider": "eastmoney",
            "market": target["market"],
            "symbol": target["symbol"],
            "provider_code": target["symbol"],
            "name": target["name"],
            "price": 25.94,
            "open_price": 25.84,
            "high_price": 26.78,
            "low_price": 25.58,
            "prev_close_price": 25.84,
            "volume": 1,
            "turnover": 2,
            "update_time": "2026-07-13T10:15:00+08:00",
            "fetched_at": time.time(),
        })

    monkeypatch.setattr("backend.api.eastmoney._fetch_eastmoney_calculator_quote", fake_fetch)
    monkeypatch.setattr("backend.core.stock.market_data.get_market_data_db_path", lambda: tmp_path / "quotes.sqlite")
    first = sync_eastmoney_calculator_market_quotes(session=session, current_user=user)
    second = sync_eastmoney_calculator_market_quotes(session=session, current_user=user)

    assert [f'{target["market"]}.{target["symbol"]}' for target in captured] == ["HK.01810"]
    assert first["downloaded_count"] == 1
    assert first["cache_hit_count"] == 0
    assert second["downloaded_count"] == 0
    assert second["cache_hit_count"] == 1
    assert second["items"][0]["price"] == 25.94


def test_calculator_quote_fetch_ignores_broken_environment_proxy(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "f43": 27.0,
                    "f44": 27.2,
                    "f45": 26.5,
                    "f46": 26.7,
                    "f47": 1,
                    "f48": 2,
                    "f58": "小米集团-W",
                    "f60": 26.6,
                    "f86": 1784168454,
                },
            }

    class FakeSession:
        def __init__(self):
            self.trust_env = True

        def get(self, url, **kwargs):
            captured.update({"url": url, "trust_env": self.trust_env, **kwargs})
            return FakeResponse()

    monkeypatch.setattr("requests.Session", FakeSession)
    quote = eastmoney._fetch_eastmoney_calculator_quote({
        "market": "HK",
        "symbol": "01810",
        "name": "小米集团",
    })

    assert captured["trust_env"] is False
    assert captured["params"]["_"] > 0
    assert quote.price == 27.0
    assert quote.update_time
    assert not quote.error
