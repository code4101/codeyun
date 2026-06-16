from backend.core.stock.qlib_bridge import QlibFactorAnalysis
from backend.core.stock.trade_advice import (
    AccountTradingContext,
    StockPositionInput,
    build_backtest_evidence,
    build_trade_candidate_advice,
    build_trade_advice,
    serialize_trade_candidate_advice,
    serialize_trade_advice_result,
)
from backend.api.eastmoney import _apply_manual_account_override, _apply_manual_position_override


def _analysis(
    *,
    score: int,
    signal: str = "观察",
    latest_close: float = 1.135,
    return_5: float = 1.0,
    return_20: float = -2.0,
    return_60: float = 12.0,
    ma_20: float = 1.15,
    ma_60: float = 1.08,
    ma_20_distance: float = -1.3,
    volume_ratio_5_20: float = 1.1,
) -> QlibFactorAnalysis:
    return QlibFactorAnalysis(
        market="SH",
        symbol="562500",
        name="机器人ETF华夏",
        qlib_symbol="sh562500",
        row_count=120,
        source="test",
        start_date="2025-01-01",
        end_date="2026-06-15",
        latest_close=latest_close,
        latest_change_percent=None,
        return_5=return_5,
        return_20=return_20,
        return_60=return_60,
        ma_5=None,
        ma_20=ma_20,
        ma_60=ma_60,
        ma_20_distance=ma_20_distance,
        volatility_20=24.0,
        max_drawdown=-12.0,
        volume_ratio_5_20=volume_ratio_5_20,
        score=score,
        signal=signal,
        model_status="测试策略摘要",
        error="",
    )


def test_holding_robot_etf_below_cost_keeps_position_until_trigger() -> None:
    result = build_trade_advice(
        StockPositionInput(
            market="SH",
            symbol="562500",
            name="机器人ETF华夏",
            quantity=23000,
            cost_price=1.18,
            current_price=1.135,
            market_value=26105,
        ),
        _analysis(score=48, signal="中性观察"),
    )

    assert result.action == "hold"
    assert "当前不卖" in result.headline
    assert "1.204" in result.next_trigger
    assert "5,000 份" in result.next_trigger
    assert result.steps[0].value == "不卖"
    assert result.operation["intent"] == "watch_sell"
    assert result.operation["trigger_price"] == 1.204
    assert result.operation["quantity"] == 5000
    assert result.operation["order_type"] == "conditional_limit"
    assert "未到 1.204 不卖" in result.operation["guardrail_text"]


def test_holding_at_cost_profit_trigger_sells_trading_lot() -> None:
    result = build_trade_advice(
        StockPositionInput(
            market="SH",
            symbol="562500",
            name="机器人ETF华夏",
            quantity=23000,
            cost_price=1.18,
            current_price=1.205,
            market_value=27715,
        ),
        _analysis(score=62, signal="中性偏强", latest_close=1.205, ma_20=1.16, ma_60=1.08, ma_20_distance=3.9),
    )

    assert result.action == "sell_plan"
    assert result.action_text == "分档卖出"
    assert "1.204" in result.primary_order
    assert "5,000 份" in result.primary_order
    assert result.steps[0].label == "第一档"
    assert result.operation["intent"] == "sell"
    assert result.operation["price"] == 1.204
    assert result.operation["quantity_text"] == "5,000 份"
    assert "未成交不追低卖" in result.operation["guardrail_text"]


def test_weak_holding_only_reduces_trading_lot_not_clear_position() -> None:
    result = build_trade_advice(
        StockPositionInput(
            market="SH",
            symbol="562500",
            name="机器人ETF华夏",
            quantity=23000,
            cost_price=1.18,
            current_price=1.06,
        ),
        _analysis(
            score=35,
            signal="转弱",
            latest_close=1.06,
            return_20=-10.0,
            ma_20=1.18,
            ma_60=1.1,
            ma_20_distance=-10.2,
        ),
    )

    assert result.action == "risk_reduce"
    assert "底仓暂不动" in result.primary_order
    assert "5,000 份" in result.primary_order


def test_no_position_strong_score_allows_first_lot_buy() -> None:
    result = build_trade_advice(
        StockPositionInput(market="SH", symbol="562500", name="机器人ETF华夏"),
        _analysis(score=76, signal="偏强", return_20=6.0, latest_close=1.2, ma_20=1.16, ma_60=1.05),
    )

    assert result.action == "buy"
    assert "首仓可买" in result.primary_order
    assert result.steps[0].label == "首仓"


def test_no_position_without_edge_waits() -> None:
    result = build_trade_advice(
        StockPositionInput(market="SH", symbol="562500", name="机器人ETF华夏"),
        _analysis(score=55, signal="观察", return_20=1.0),
    )

    assert result.action == "buy_watch"
    assert result.steps[0].value == "不买"


def test_trade_advice_serializes_backtest_benchmark_evidence() -> None:
    backtest = build_backtest_evidence(
        strategy_id="score70",
        strategy_name="Qlib综合分一手评分",
        start_date="2025-01-01",
        end_date="2026-06-15",
        total_return_percent=18.2,
        benchmark_name="上证指数",
        benchmark_return_percent=6.4,
        trade_count=9,
    )
    result = build_trade_advice(
        StockPositionInput(
            market="SH",
            symbol="562500",
            name="机器人ETF华夏",
            quantity=23000,
            cost_price=1.18,
            current_price=1.135,
        ),
        _analysis(score=48, signal="中性观察"),
        backtests=(backtest,),
    )

    payload = serialize_trade_advice_result(result)

    assert payload["backtests"][0]["strategy_id"] == "score70"
    assert payload["backtests"][0]["benchmark_name"] == "上证指数"
    assert round(payload["backtests"][0]["excess_return_percent"], 2) == 11.8
    assert "超额 +11.80%" in payload["backtests"][0]["summary"]
    assert payload["event_evidence"]
    assert payload["event_evidence"][0]["impact"] == "support"
    assert "机器人" in payload["event_evidence"][0]["title"]
    assert any(item["impact"] == "risk" for item in payload["event_evidence"])


def test_trade_candidate_strong_no_position_allows_first_lot_buy() -> None:
    backtest = build_backtest_evidence(
        strategy_id="score70",
        strategy_name="Qlib综合分一手评分",
        start_date="2025-01-01",
        end_date="2026-06-15",
        total_return_percent=18.2,
        benchmark_name="上证指数",
        benchmark_return_percent=6.4,
        trade_count=9,
    )
    result = build_trade_candidate_advice(
        _analysis(
            score=78,
            signal="偏强",
            latest_close=12.3,
            return_20=8.0,
            return_60=18.0,
            ma_20=11.8,
            ma_60=10.9,
            ma_20_distance=4.2,
            volume_ratio_5_20=1.5,
        ),
        has_position=False,
        backtests=(backtest,),
        budget=5000,
    )
    payload = serialize_trade_candidate_advice(result)

    assert payload["action"] == "buy"
    assert "首仓" in payload["primary_order"]
    assert payload["rank_score"] > 80
    assert payload["operation"]["intent"] == "buy"
    assert payload["operation"]["side"] == "buy"
    assert payload["operation"]["quantity"] > 0
    assert "不一次打满" in payload["operation"]["guardrail_text"]
    assert [step["label"] for step in payload["steps"]] == ["首仓", "加仓", "失效"]
    assert "不一次打满" in payload["steps"][0]["note"]
    assert payload["backtests"][0]["benchmark_name"] == "上证指数"
    assert round(payload["backtests"][0]["excess_return_percent"], 2) == 11.8
    assert "超额 +11.80%" in payload["backtests"][0]["summary"]
    assert payload["event_evidence"]
    assert payload["event_evidence"][0]["source"]


def test_trade_candidate_existing_position_is_not_new_buy_candidate() -> None:
    result = build_trade_candidate_advice(
        _analysis(score=90, signal="偏强", latest_close=10.0, return_20=12.0),
        has_position=True,
        position_quantity=1000,
    )

    assert result.action == "hold"
    assert "不作为新买候选" in result.headline
    assert result.rank_score < 0


def test_trade_candidate_first_lot_budget_uses_account_constraints() -> None:
    result = build_trade_candidate_advice(
        _analysis(score=80, signal="偏强", latest_close=10.0, return_20=8.0),
        has_position=False,
        account=AccountTradingContext(total_asset=30000, cash_available=2000),
    )
    payload = serialize_trade_candidate_advice(result)

    assert result.action == "buy_watch"
    assert result.action_text == "现金不足"
    assert payload["account"]["first_lot_budget"] == 700
    assert payload["operation"]["order_type"] == "budget_wait"
    assert payload["operation"]["cash_budget"] == 700
    assert "可用现金 2,000.00" in result.evidence[0]


def test_trade_advice_serializes_account_position_weight() -> None:
    result = build_trade_advice(
        StockPositionInput(
            market="SH",
            symbol="562500",
            name="机器人ETF华夏",
            quantity=23000,
            cost_price=1.18,
            current_price=1.135,
            market_value=26105,
        ),
        _analysis(score=48, signal="中性观察"),
        account=AccountTradingContext(total_asset=40000, cash_available=3000),
    )
    payload = serialize_trade_advice_result(result)

    assert round(payload["account"]["position_weight_percent"], 2) == 65.26
    assert any("高于单票上限" in item for item in payload["evidence"])


def test_manual_position_override_recalculates_holding_maintenance() -> None:
    position = _apply_manual_position_override(
        StockPositionInput(market="SH", symbol="562500", name="机器人ETF华夏"),
        market="SH",
        symbol="562500",
        name="机器人ETF华夏",
        quantity=23000,
        cost_price=1.18,
        current_price=1.135,
    )
    result = build_trade_advice(position, _analysis(score=48, signal="中性观察"))

    assert position.market_value == 26105
    assert result.action == "hold"
    assert result.operation["trigger_price"] == 1.204
    assert result.operation["quantity"] == 5000
    assert "未到 1.204 不卖" in result.operation["guardrail_text"]


def test_manual_account_override_keeps_policy_limits() -> None:
    context = _apply_manual_account_override(
        AccountTradingContext(
            total_asset=None,
            cash_available=None,
            max_single_position_percent=0.25,
            first_lot_cash_percent=0.35,
            first_lot_asset_percent=0.05,
            max_first_lot_budget=5000,
        ),
        total_asset=100000,
        cash_available=23000,
    )

    assert context.total_asset == 100000
    assert context.cash_available == 23000
    assert context.max_single_position_percent == 0.25
    assert context.first_lot_cash_percent == 0.35
