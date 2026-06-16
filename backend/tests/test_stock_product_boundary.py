import json
from pathlib import Path

import pytest

from backend.api.eastmoney import (
    _dedupe_position_snapshots,
    _position_quantity_by_key,
    router as eastmoney_router,
)


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_SRC = ROOT / "frontend" / "src"


def _read_frontend(relative_path: str) -> str:
    return (FRONTEND_SRC / relative_path).read_text(encoding="utf-8")


def _schema_flags_by_path() -> dict[str, bool]:
    return {
        route.path: bool(getattr(route, "include_in_schema", True))
        for route in eastmoney_router.routes
        if hasattr(route, "path")
    }


def test_eastmoney_openapi_centers_trade_advice_not_research_tools() -> None:
    schema_flags = _schema_flags_by_path()

    public_operation_paths = {
        "/qlib/analysis",
        "/qlib/export",
        "/trade-advice",
        "/trade-candidates",
        "/trade-workbench",
        "/market-history/akshare",
        "/market-intraday/akshare",
    }
    for path in public_operation_paths:
        assert schema_flags[path] is True

    hidden_research_paths = {
        "/strategy-research",
        "/strategy-research/{strategy_id}",
        "/strategy-research-backlog",
        "/qlib/screen/hk-pool",
        "/qlib/backtest/one-lot-score",
        "/qlib/hk-connect-momentum-review",
        "/qlib/backtest/hk-pool-one-lot-score",
        "/qlib/backtest/hk-pool-strategy-search",
        "/qlib/backtest/hk-pool-rotation-strategy-search",
        "/qlib/backtest/cross-asset-etf-canary-rotation",
    }
    for path in hidden_research_paths:
        assert schema_flags[path] is False


def test_eastmoney_menu_only_exposes_stock_operation_advice() -> None:
    registry_path = FRONTEND_SRC / "features" / "access" / "permissionRegistry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    items = registry["nodes"] if isinstance(registry, dict) else registry
    eastmoney = next(item for item in items if item["key"] == "notes.eastmoney")

    assert eastmoney["title"] == "股票操作建议"
    assert eastmoney["menu_paths"] == ["/notes/eastmoney/trade"]
    assert "/notes/eastmoney/robot-history" in eastmoney["route_paths"]
    assert "/notes/eastmoney/sync" in eastmoney["route_paths"]


def test_frontend_eastmoney_api_exposes_operation_advice_not_research_tools() -> None:
    api_text = _read_frontend("api/eastmoney.ts")

    required_operation_exports = [
        "fetchEastmoneyTradeAdvice",
        "fetchEastmoneyTradeCandidates",
        "fetchEastmoneyTradeWorkbench",
    ]
    for name in required_operation_exports:
        assert name in api_text

    forbidden_research_exports = [
        "fetchEastmoneyStrategyResearch",
        "fetchEastmoneyQlibHkPoolStrategySearch",
        "fetchEastmoneyQlibHkPoolRotationStrategySearch",
        "fetchEastmoneyCrossAssetEtfCanaryRotation",
        "fetchEastmoneyHkConnectMomentumReview",
        "EastmoneyStrategyResearch",
        "EastmoneyEtfRotation",
    ]
    for name in forbidden_research_exports:
        assert name not in api_text

    forbidden_public_paths = [
        "strategy-research",
        "hk-pool-strategy-search",
        "hk-pool-rotation-strategy-search",
        "cross-asset-etf-canary-rotation",
        "hk-connect-momentum-review",
    ]
    for path in forbidden_public_paths:
        assert path not in api_text


def test_trade_page_centers_action_advice_with_evidence_not_auxiliary_research() -> None:
    page_text = _read_frontend("standard/notes/eastmoney/trade/page.vue")

    assert "<h1>股票操作建议</h1>" in page_text
    assert "账户操作清单" in page_text
    assert "当前计算口径" in page_text
    assert "可买候选" in page_text
    assert "<th>价格 / 数量 / 金额</th>" in page_text
    assert "<th>触发 / 风控</th>" in page_text
    assert "<th>依据</th>" in page_text
    assert "row.operationPriceText" in page_text
    assert "row.operationQuantityText" in page_text
    assert "row.operationAmountText" in page_text
    assert "row.eventText" in page_text
    assert "selectedTradeAction.strategyBasisText" in page_text
    assert "selectedTradeAction.planSteps.length" in page_text
    assert "function previewTradeAction(row: TradeWorkbenchActionRow)" in page_text
    assert "selectedTradeActionKey.value = row.key" in page_text
    assert "step.value" in page_text
    assert "step.note" in page_text
    assert "策略依据" in page_text
    assert "selectedTradeAction.strategyBasisText" in page_text
    assert "新闻政策" in page_text
    assert "formatTradeEventSummary" in page_text
    assert "formatTradeEventSummary(selectedTradeAction.item.event_evidence)" in page_text
    assert "event_evidence" in page_text
    assert "策略依据和回测" in page_text
    assert "历史回测" in page_text
    assert "指数对比" in page_text

    assert "辅助研究" not in page_text
    assert "跨资产ETF轮动" not in page_text
    assert "策略研究" not in page_text
    assert "港股池候选" not in page_text


def test_duplicate_position_snapshots_keep_one_effective_holding() -> None:
    rows = [
        {
            "market": "HK",
            "security_code": "01810",
            "security_name": "小米集团",
            "quantity": 7800,
            "cost_price": 44.964,
            "current_price": 25.0,
            "market_value": 195000,
        },
        {
            "market": "HK",
            "security_code": "001810",
            "security_name": "小米集团",
            "quantity": 7800,
            "cost_price": 44.964,
            "current_price": 25.0,
            "market_value": 195000,
        },
    ]

    deduped = _dedupe_position_snapshots(rows)

    assert len(deduped) == 1
    assert deduped[0]["quantity"] == 7800
    assert _position_quantity_by_key(deduped)[("HK", "01810")] == 7800


def test_split_position_snapshots_merge_into_one_effective_holding() -> None:
    rows = [
        {
            "market": "HK",
            "security_code": "01810",
            "security_name": "小米集团",
            "quantity": 1000,
            "cost_price": 10.0,
            "current_price": 11.0,
            "market_value": 11000,
        },
        {
            "market": "HK",
            "security_code": "001810",
            "security_name": "小米集团-W",
            "quantity": 500,
            "cost_price": 12.0,
            "current_price": 13.0,
            "market_value": 6500,
        },
    ]

    deduped = _dedupe_position_snapshots(rows)

    assert len(deduped) == 1
    assert deduped[0]["quantity"] == 1500
    assert deduped[0]["market_value"] == 17500
    assert deduped[0]["cost_price"] == pytest.approx(10.6666667)
    assert _position_quantity_by_key(deduped)[("HK", "01810")] == 1500
