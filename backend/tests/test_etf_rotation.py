import pytest

from backend.core.stock.etf_rotation import (
    EtfRotationHolding,
    apply_etf_rotation_volatility_target_overlay,
    compute_cross_asset_etf_breadth_filtered_canary_rotation,
    compute_cross_asset_etf_canary_rotation,
    compute_cross_asset_etf_dual_momentum_switch,
    compute_cross_asset_etf_hybrid_asset_allocation,
    compute_cross_asset_etf_inverse_variance_sleeve,
    compute_nasdaq_etf_turn_of_month_strategy,
    evaluate_etf_premium_execution_guard,
    serialize_etf_rotation_backtest_result,
)
from backend.core.stock.market_data import connect_market_data_db
from backend.api.eastmoney import get_eastmoney_cross_asset_etf_canary_rotation_backtest


def _has_etf_rotation_fixture_data() -> bool:
    with connect_market_data_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT symbol) AS symbols, MAX(time_key) AS max_date
            FROM market_kline
            WHERE provider = 'akshare'
              AND market IN ('SH', 'SZ')
              AND symbol IN (
                '510300', '510500', '588000', '159915', '513520', '513500', '513100',
                '513180', '515220', '512400', '512800', '518880', '511260', '511010'
              )
              AND ktype = 'daily'
            """
        ).fetchone()
    return bool(row and row["symbols"] >= 14 and str(row["max_date"] or "") >= "2026-06-12")


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_cross_asset_etf_canary_rotation_reproduces_current_research_candidate():
    result = compute_cross_asset_etf_canary_rotation()

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 8.50,
        "2022": 4.76,
        "2023": 5.25,
        "2024": 18.28,
        "2025": 6.46,
        "2026": 1.71,
    }
    assert round(result.total_return * 100, 2) == 53.23
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert result.latest_signal.holdings


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_etf_rotation_serialization_contains_latest_signal():
    payload = serialize_etf_rotation_backtest_result(compute_cross_asset_etf_canary_rotation())

    assert payload["strategy_id"] == "cross_asset_etf_weekly_relative_momentum_abs_filter"
    assert payload["period_count"] > 0
    assert payload["latest_signal"]["date"] == "2026-06-12"
    assert payload["latest_signal"]["holdings"]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_etf_rotation_api_function_returns_default_backtest_payload():
    payload = get_eastmoney_cross_asset_etf_canary_rotation_backtest(
        refresh=True,
        progress=False,
        start_date="2021-01-01",
        hold_days=10,
        top_n=1,
        cost=0.001,
        canary_threshold=0.5,
    )

    assert round(payload["annual_returns"]["2026"] * 100, 2) == 1.71
    assert payload["latest_signal"]["date"] == "2026-06-12"

    cached = get_eastmoney_cross_asset_etf_canary_rotation_backtest(
        refresh=False,
        progress=True,
        start_date="2021-01-01",
        hold_days=10,
        top_n=1,
        cost=0.001,
        canary_threshold=0.5,
    )

    assert cached["latest_signal"]["date"] == "2026-06-12"
    assert cached["annual_returns"] == payload["annual_returns"]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_cross_asset_etf_hybrid_asset_allocation_reproduces_research_candidate():
    result = compute_cross_asset_etf_hybrid_asset_allocation()

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 13.05,
        "2022": 1.55,
        "2023": 8.53,
        "2024": 22.30,
        "2025": 30.16,
        "2026": 6.48,
    }
    assert round(result.total_return * 100, 2) == 111.17
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert [(item.symbol, round(item.weight, 2)) for item in result.latest_signal.holdings] == [("511260", 1.0)]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_etf_rotation_volatility_target_overlay_reduces_canary_strategy_exposure():
    result = apply_etf_rotation_volatility_target_overlay(
        compute_cross_asset_etf_canary_rotation(),
        lookback_periods=3,
        target_annual_volatility=0.08,
    )

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 7.25,
        "2022": 5.56,
        "2023": 4.43,
        "2024": 16.84,
        "2025": 0.46,
        "2026": 9.29,
    }
    assert round(result.total_return * 100, 2) == 51.67
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert round(result.latest_signal.cash_fraction, 4) == 0.8531
    assert [(item.symbol, round(item.weight, 4)) for item in result.latest_signal.holdings] == [
        ("513520", 0.1469),
        ("511260", 0.1469),
    ]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_nasdaq_etf_turn_of_month_strategy_is_cost_sensitive_calendar_sleeve():
    result = compute_nasdaq_etf_turn_of_month_strategy(cost=0.0005)

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 3.95,
        "2022": 3.97,
        "2023": 5.12,
        "2024": 3.55,
        "2025": 2.84,
        "2026": 7.33,
    }
    assert round(result.total_return * 100, 2) == 29.85
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-05-29"

    high_cost = compute_nasdaq_etf_turn_of_month_strategy(cost=0.002)
    high_cost_annual_percent = {year: round(value * 100, 2) for year, value in high_cost.annual_returns.items()}
    assert min(high_cost_annual_percent.values()) < 0


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_cross_asset_etf_inverse_variance_sleeve_is_defensive_bond_heavy_candidate():
    result = compute_cross_asset_etf_inverse_variance_sleeve()

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 4.81,
        "2022": 0.34,
        "2023": 5.19,
        "2024": 11.15,
        "2025": 2.00,
        "2026": 0.98,
    }
    assert round(result.total_return * 100, 2) == 26.64
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert [(item.symbol, round(item.weight, 4)) for item in result.latest_signal.holdings[:2]] == [
        ("511010", 0.7172),
        ("511260", 0.2750),
    ]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_cross_asset_etf_dual_momentum_switch_is_promising_but_premium_limited_candidate():
    result = compute_cross_asset_etf_dual_momentum_switch()

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 2.20,
        "2022": 5.65,
        "2023": 20.69,
        "2024": 4.32,
        "2025": 27.20,
        "2026": 5.92,
    }
    assert round(result.total_return * 100, 2) == 83.17
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert [(item.symbol, round(item.weight, 2)) for item in result.latest_signal.holdings] == [("513520", 1.0)]


@pytest.mark.skipif(not _has_etf_rotation_fixture_data(), reason="local ETF market_kline data is not populated")
def test_cross_asset_etf_breadth_filtered_canary_rotation_improves_total_return():
    result = compute_cross_asset_etf_breadth_filtered_canary_rotation()

    annual_percent = {year: round(value * 100, 2) for year, value in result.annual_returns.items()}

    assert annual_percent == {
        "2021": 9.97,
        "2022": 7.99,
        "2023": 10.82,
        "2024": 22.88,
        "2025": 5.73,
        "2026": 1.71,
    }
    assert round(result.total_return * 100, 2) == 73.91
    assert result.latest_signal is not None
    assert result.latest_signal.date == "2026-06-12"
    assert [(item.symbol, round(item.weight, 2)) for item in result.latest_signal.holdings] == [
        ("513520", 0.5),
        ("511260", 0.5),
    ]


def test_etf_premium_execution_guard_blocks_high_premium_buys():
    holdings = (
        EtfRotationHolding("SH", "513520", "日经ETF华夏", 0.5, 0.0),
        EtfRotationHolding("SH", "511260", "十年国债ETF", 0.5, 0.0),
    )

    items = evaluate_etf_premium_execution_guard(
        holdings,
        discount_rate_by_symbol={
            "513520": -5.14,
            "511260": 0.09,
        },
        max_premium_percent=2.0,
    )

    assert [(item.symbol, item.action) for item in items] == [
        ("513520", "block_high_premium"),
        ("511260", "allow"),
    ]
    assert round(items[0].premium_rate_percent or 0, 2) == 5.14


def test_etf_premium_execution_guard_marks_missing_data_for_review():
    holdings = (
        EtfRotationHolding("SH", "513520", "日经ETF华夏", 0.5, 0.0),
        EtfRotationHolding("SH", "511260", "十年国债ETF", 0.5, 0.0),
    )

    items = evaluate_etf_premium_execution_guard(
        holdings,
        discount_rate_by_symbol={},
        max_premium_percent=2.0,
    )

    assert {item.action for item in items} == {"review_missing_premium"}
