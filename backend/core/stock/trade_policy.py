from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .qlib_bridge import QlibWatchTarget


@dataclass(frozen=True)
class TradeCandidatePoolDefinition:
    key: str
    name: str
    source: str
    description: str
    targets: tuple[QlibWatchTarget, ...] = ()
    start_date: str = "1990-01-01"


@dataclass(frozen=True)
class TradeWorkbenchPolicy:
    key: str = "standard_trade_maintenance"
    name: str = "标准持仓维护策略"
    max_single_position_percent: float = 0.25
    first_lot_cash_percent: float = 0.35
    first_lot_asset_percent: float = 0.05
    max_first_lot_budget: float = 5000
    score_threshold: int = 70
    take_profit_percent: float = 8.0
    stop_loss_percent: float = 8.0
    cost_rate: float = 0.01
    score_profile: str = "balanced"


WATCHLIST_CANDIDATE_POOL = TradeCandidatePoolDefinition(
    key="watchlist",
    name="自选候选",
    source="watchlist:default",
    description="优先覆盖当前真实关注的机器人、云和硬件链条标的。",
    targets=(
        QlibWatchTarget(market="SH", symbol="562500", name="机器人ETF华夏", start_date="2024-01-01"),
        QlibWatchTarget(market="SZ", symbol="159278", name="机器人PH", start_date="1990-01-01"),
        QlibWatchTarget(market="HK", symbol="03896", name="金山云", start_date="1990-01-01"),
        QlibWatchTarget(market="HK", symbol="01810", name="小米集团", start_date="1990-01-01"),
    ),
)

HK_POOL_CANDIDATE_POOL = TradeCandidatePoolDefinition(
    key="hk_pool",
    name="可买候选",
    source="hk_pool:qlib_screen",
    description="用港股池日线评分筛出未持仓的可买首仓候选。",
    start_date="1990-01-01",
)

TRADE_CANDIDATE_POOLS = {
    WATCHLIST_CANDIDATE_POOL.key: WATCHLIST_CANDIDATE_POOL,
    HK_POOL_CANDIDATE_POOL.key: HK_POOL_CANDIDATE_POOL,
}

DEFAULT_TRADE_WORKBENCH_POLICY = TradeWorkbenchPolicy()


def get_trade_candidate_pool(key: str) -> TradeCandidatePoolDefinition:
    try:
        return TRADE_CANDIDATE_POOLS[key]
    except KeyError as exc:
        supported = " / ".join(TRADE_CANDIDATE_POOLS)
        raise ValueError(f"当前只支持 {supported} 候选池") from exc


def serialize_trade_workbench_policy(policy: TradeWorkbenchPolicy = DEFAULT_TRADE_WORKBENCH_POLICY) -> dict[str, Any]:
    return {
        "key": policy.key,
        "name": policy.name,
        "max_single_position_percent": policy.max_single_position_percent * 100,
        "first_lot_cash_percent": policy.first_lot_cash_percent * 100,
        "first_lot_asset_percent": policy.first_lot_asset_percent * 100,
        "max_first_lot_budget": policy.max_first_lot_budget,
        "score_threshold": policy.score_threshold,
        "score_profile": policy.score_profile,
        "take_profit_percent": policy.take_profit_percent,
        "stop_loss_percent": policy.stop_loss_percent,
        "cost_rate_percent": policy.cost_rate * 100,
        "rules": [
            f"持仓维护：先看持仓、成本、当前仓位，再看日线综合分。",
            f"已有成本价时，第一卖档按成本上方约 2% 计划卖机动仓；不到触发价不主动割肉。",
            f"单票仓位默认不超过 {policy.max_single_position_percent * 100:.0f}%。",
            f"候选首仓不超过可用现金 {policy.first_lot_cash_percent * 100:.0f}%、总资产 {policy.first_lot_asset_percent * 100:.0f}% 和 {policy.max_first_lot_budget:.0f} 元。",
            f"回测依据使用 {policy.score_profile} 综合分，分数达到 {policy.score_threshold} 后试算一手策略。",
        ],
    }


def serialize_trade_candidate_pool(pool: TradeCandidatePoolDefinition) -> dict[str, Any]:
    return {
        "key": pool.key,
        "name": pool.name,
        "source": pool.source,
        "description": pool.description,
        "start_date": pool.start_date,
        "targets": [
            {
                "market": target.market,
                "symbol": target.symbol,
                "name": target.name,
                "start_date": target.start_date,
            }
            for target in pool.targets
        ],
    }
