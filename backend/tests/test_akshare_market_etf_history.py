import pandas as pd

from backend.core.stock.akshare_market import (
    _looks_like_cn_fund_symbol,
    _request_hk_intraday_rows_from_yahoo,
    _request_akshare_etf_intraday_rows_with_curl_transport,
    _request_akshare_history_rows,
)


class _FakeAkshare:
    def fund_etf_hist_em(self, **_kwargs):
        raise RuntimeError("eastmoney unavailable")

    def fund_etf_hist_sina(self, *, symbol: str):
        assert symbol == "sh510300"
        return pd.DataFrame(
            [
                {
                    "date": "2026-06-11",
                    "open": 4.766,
                    "high": 4.793,
                    "low": 4.718,
                    "close": 4.751,
                    "volume": 416068768,
                    "amount": 1975529542,
                },
                {
                    "date": "2026-06-12",
                    "open": 4.810,
                    "high": 4.846,
                    "low": 4.789,
                    "close": 4.818,
                    "volume": 780885896,
                    "amount": 3761995011,
                },
            ]
        )

    def fund_etf_hist_min_em(self, **_kwargs):
        raise RuntimeError("eastmoney minute unavailable")

    def stock_zh_a_minute(self, *, symbol: str, period: str, adjust: str):
        assert symbol == "sz159278"
        assert period == "1"
        assert adjust == ""
        return pd.DataFrame(
            [
                {
                    "day": "2026-06-11 14:59:00",
                    "open": 1.0,
                    "high": 1.1,
                    "low": 0.9,
                    "close": 1.05,
                    "volume": 100,
                },
                {
                    "day": "2026-06-12 09:31:00",
                    "open": 1.1,
                    "high": 1.2,
                    "low": 1.0,
                    "close": 1.15,
                    "volume": 200,
                },
                {
                    "day": "2026-06-12 15:00:00",
                    "open": 1.2,
                    "high": 1.3,
                    "low": 1.1,
                    "close": 1.25,
                    "volume": 300,
                },
            ]
        )


def test_cn_fund_symbol_detection():
    assert _looks_like_cn_fund_symbol("510300")
    assert _looks_like_cn_fund_symbol("159915")
    assert _looks_like_cn_fund_symbol("588000")
    assert not _looks_like_cn_fund_symbol("600000")
    assert not _looks_like_cn_fund_symbol("000001")


def test_cn_etf_history_falls_back_to_sina_when_eastmoney_fails():
    rows = _request_akshare_history_rows(
        _FakeAkshare(),
        market="SH",
        symbol="510300",
        period="daily",
        start_date="20260601",
        end_date="20260630",
        adjust="",
    )

    assert len(rows) == 2
    assert rows[-1].date == "2026-06-12"
    assert rows[-1].close == 4.818
    assert rows[-1].amount == 3761995011


def test_cn_etf_intraday_falls_back_to_sina_when_eastmoney_fails():
    rows = _request_akshare_etf_intraday_rows_with_curl_transport(
        _FakeAkshare(),
        market="SZ",
        symbol="159278",
        start_date="2026-06-12 09:30:00",
        end_date="2026-06-12 15:00:00",
        period="1",
    )

    assert len(rows) == 2
    assert rows[0].time == "2026-06-12 09:31:00"
    assert rows[0].close == 1.15
    assert rows[-1].time == "2026-06-12 15:00:00"
    assert rows[-1].volume == 300


def test_hk_intraday_yahoo_fallback_filters_target_window(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [
                                1781228940,  # 2026-06-12 09:49:00 +08:00
                                1781229000,  # 2026-06-12 09:50:00 +08:00
                                1781229060,  # 2026-06-12 09:51:00 +08:00
                            ],
                            "indicators": {
                                "quote": [
                                    {
                                        "open": [1.0, 1.1, 1.2],
                                        "high": [1.1, 1.2, 1.3],
                                        "low": [0.9, 1.0, 1.1],
                                        "close": [1.05, 1.15, 1.25],
                                        "volume": [100, 200, 300],
                                    }
                                ]
                            },
                        }
                    ]
                }
            }

    def fake_get(url, *, params, headers, timeout):
        assert url.endswith("/3896.HK")
        assert params == {"range": "5d", "interval": "1m"}
        assert headers["User-Agent"]
        assert timeout == 20
        return FakeResponse()

    import requests

    monkeypatch.setattr(requests, "get", fake_get)

    rows = _request_hk_intraday_rows_from_yahoo(
        symbol="03896",
        start_date="2026-06-12 09:50:00",
        end_date="2026-06-12 09:51:00",
        period="1",
    )

    assert [row.time for row in rows] == ["2026-06-12 09:50:00", "2026-06-12 09:51:00"]
    assert rows[0].close == 1.15
    assert rows[-1].volume == 300
