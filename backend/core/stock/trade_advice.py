from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .qlib_bridge import QLIB_FACTOR_SCORE_RULES, QlibFactorAnalysis


TradeAdviceAction = str


@dataclass(frozen=True)
class AccountTradingContext:
    total_asset: float | None = None
    cash_available: float | None = None
    max_single_position_percent: float = 0.25
    first_lot_cash_percent: float = 0.35
    first_lot_asset_percent: float = 0.05
    max_first_lot_budget: float = 5000

    def with_policy(
        self,
        *,
        max_single_position_percent: float,
        first_lot_cash_percent: float,
        first_lot_asset_percent: float,
        max_first_lot_budget: float,
    ) -> AccountTradingContext:
        return AccountTradingContext(
            total_asset=self.total_asset,
            cash_available=self.cash_available,
            max_single_position_percent=max_single_position_percent,
            first_lot_cash_percent=first_lot_cash_percent,
            first_lot_asset_percent=first_lot_asset_percent,
            max_first_lot_budget=max_first_lot_budget,
        )


@dataclass(frozen=True)
class StockPositionInput:
    market: str
    symbol: str
    name: str = ""
    quantity: float | None = None
    cost_price: float | None = None
    current_price: float | None = None
    market_value: float | None = None


@dataclass(frozen=True)
class TradeAdviceStep:
    label: str
    value: str
    note: str


@dataclass(frozen=True)
class TradeAdviceBacktestEvidence:
    strategy_id: str
    strategy_name: str
    start_date: str
    end_date: str
    total_return_percent: float | None
    benchmark_name: str
    benchmark_return_percent: float | None
    excess_return_percent: float | None
    trade_count: int
    summary: str
    error: str = ""


@dataclass(frozen=True)
class TradeEventEvidence:
    title: str
    event_date: str
    source: str
    url: str
    impact: str
    summary: str


@dataclass(frozen=True)
class TradeCandidateAdvice:
    market: str
    symbol: str
    name: str
    action: TradeAdviceAction
    action_text: str
    headline: str
    primary_order: str
    next_trigger: str
    risk_line: str
    operation: dict[str, Any]
    evidence: tuple[str, ...]
    steps: tuple[TradeAdviceStep, ...]
    strategy_score: int | None
    backtests: tuple[TradeAdviceBacktestEvidence, ...]
    current_price: float | None
    rank_score: float
    account: dict[str, Any]
    source: str
    event_evidence: tuple[TradeEventEvidence, ...] = ()


@dataclass(frozen=True)
class TradeAdviceResult:
    market: str
    symbol: str
    name: str
    action: TradeAdviceAction
    action_text: str
    headline: str
    primary_order: str
    next_trigger: str
    risk_line: str
    recovery_line: str
    operation: dict[str, Any]
    evidence: tuple[str, ...]
    steps: tuple[TradeAdviceStep, ...]
    strategy_status: str
    strategy_score: int | None
    strategy_rules: tuple[str, ...]
    backtests: tuple[TradeAdviceBacktestEvidence, ...]
    position: dict[str, Any]
    account: dict[str, Any]
    source: str
    event_evidence: tuple[TradeEventEvidence, ...] = ()


def build_trade_advice(
    position: StockPositionInput,
    analysis: QlibFactorAnalysis | None,
    backtests: tuple[TradeAdviceBacktestEvidence, ...] = (),
    account: AccountTradingContext | None = None,
) -> TradeAdviceResult:
    market = _normalize_market(position.market)
    symbol = _normalize_symbol(position.symbol)
    name = position.name.strip() or analysis.name if analysis is not None else position.name.strip()
    price = _first_number(position.current_price, analysis.latest_close if analysis is not None else None)
    quantity = _number_or_zero(position.quantity)
    cost_price = _number_or_none(position.cost_price)
    market_value = _first_number(position.market_value, quantity * price if quantity > 0 and price is not None else None)
    score = analysis.score if analysis is not None else None
    signal = analysis.signal if analysis is not None and analysis.signal else "等待评分"
    strategy_status = analysis.model_status if analysis is not None else "等待 Qlib 日线摘要"

    position_payload = _position_payload(
        quantity=quantity,
        cost_price=cost_price,
        current_price=price,
        market_value=market_value,
    )
    account_payload = _account_payload(account, position_market_value=market_value)

    if price is None or price <= 0:
        return TradeAdviceResult(
            market=market,
            symbol=symbol,
            name=name,
            action="no_data",
            action_text="等待数据",
            headline="还没有足够行情数据生成买卖建议。",
            primary_order="先加载日线和账户持仓。",
            next_trigger="-",
            risk_line="-",
            recovery_line="-",
            operation=_operation_payload(
                intent="none",
                side="none",
                order_type="none",
                summary="先加载日线和账户持仓。",
                lot_size=100,
                guardrail_text="数据不完整时不生成下单动作。",
            ),
            evidence=("行情或评分数据不足。",),
            steps=(),
            strategy_status=strategy_status,
            strategy_score=score,
            strategy_rules=QLIB_FACTOR_SCORE_RULES,
            backtests=backtests,
            position=position_payload,
            account=account_payload,
            source="trade_advice.v1",
        )

    has_position = quantity > 0
    lot_size = 200 if market == "HK" else 100
    sell_quantity = _planned_sell_quantity(quantity, lot_size)
    ma20 = analysis.ma_20 if analysis is not None else None
    ma60 = analysis.ma_60 if analysis is not None else None
    ma20_distance = analysis.ma_20_distance if analysis is not None else None
    return5 = analysis.return_5 if analysis is not None else None
    return20 = analysis.return_20 if analysis is not None else None
    return60 = analysis.return_60 if analysis is not None else None
    volume_ratio = analysis.volume_ratio_5_20 if analysis is not None else None

    base_sell_trigger = (
        cost_price * 1.02
        if cost_price is not None
        else max(price * 1.08, ma20 * 1.05 if ma20 is not None else 0)
    )
    sell_trigger = _round_price(base_sell_trigger)
    second_sell_trigger = _round_price(sell_trigger * 1.025)
    recovery_line = _round_price(max(ma20 if ma20 is not None else price * 0.98, cost_price * 0.98 if cost_price is not None else 0))
    risk_line = _round_price(min(ma60 * 0.98 if ma60 is not None else price * 0.92, price * 0.92))
    pnl_percent = (price / cost_price - 1) * 100 if cost_price else None

    factor_evidence = (
        "综合策略分暂不可用。" if score is None else f"标准综合策略：{signal}，{score} 分。",
        f"{_nullable_percent_text('5日', return5)}，{_nullable_percent_text('20日', return20)}，{_nullable_percent_text('60日', return60)}。",
        "均线位置暂不可用。" if ma20_distance is None else f"相对20日线 {_signed_percent(ma20_distance)}，不是默认卖出依据。",
        "量能数据暂不可用。" if volume_ratio is None else f"5/20日量能比 {_ratio(volume_ratio)}，用于判断是否过热。",
    )

    if has_position:
        if score is not None and score <= 40 and ma20_distance is not None and ma20_distance < -8:
            return TradeAdviceResult(
                market=market,
                symbol=symbol,
                name=name,
                action="risk_reduce",
                action_text="风险减仓",
                headline=f"持有 {_share_quantity(quantity)}，当前策略转弱，先处理机动仓。",
                primary_order=f"可卖 {_share_quantity(sell_quantity)}，底仓暂不动。",
                next_trigger=f"若收盘仍弱于20日线 {_number(ma20)}，继续降机动仓。",
                risk_line=f"{_number(risk_line)} 附近是下一道风控线。",
                recovery_line=f"重新站回 {_number(recovery_line)} 后再考虑接回。",
                operation=_operation_payload(
                    intent="risk_reduce",
                    side="sell",
                    order_type="market_or_limit",
                    quantity=sell_quantity,
                    stop_price=risk_line,
                    recovery_price=recovery_line,
                    lot_size=lot_size,
                    summary=f"卖 {_share_quantity(sell_quantity)}，仅处理机动仓。",
                    guardrail_text="只处理机动仓，不清仓；修复到恢复线后停止减仓。",
                ),
                evidence=factor_evidence,
                steps=(
                    TradeAdviceStep("现在", f"卖 {_share_quantity(sell_quantity)}", "仅处理机动仓，不做清仓动作。"),
                    TradeAdviceStep("修复", _number(recovery_line), "站回后停止减仓。"),
                    TradeAdviceStep("失效", _number(risk_line), "跌破后重新计算仓位上限。"),
                ),
                strategy_status=strategy_status,
                strategy_score=score,
                strategy_rules=QLIB_FACTOR_SCORE_RULES,
                backtests=backtests,
                position=position_payload,
                account=account_payload,
                source="trade_advice.v1",
        )

        cost_evidence = (
            f"成本 {_number(cost_price)}，当前浮动 {_signed_percent(pnl_percent)}。"
            if cost_price is not None and pnl_percent is not None
            else "未读取到成本价，按行情信号维护。"
        )
        if price >= sell_trigger:
            return TradeAdviceResult(
                market=market,
                symbol=symbol,
                name=name,
                action="sell_plan",
                action_text="分档卖出",
                headline=f"持有 {_share_quantity(quantity)}，已到第一卖档，按计划卖机动仓。",
                primary_order=f"可挂 {_number(sell_trigger)} 附近卖 {_share_quantity(sell_quantity)}，不要一次清仓。",
                next_trigger=f"{_number(second_sell_trigger)} 以上再看第二档；未成交不追低卖。",
                risk_line=f"{_number(risk_line)} 有效跌破才进入风控减仓。",
                recovery_line=f"卖出后回落到 {_number(recovery_line)} 附近再考虑接回。",
                operation=_operation_payload(
                    intent="sell",
                    side="sell",
                    order_type="limit",
                    price=sell_trigger,
                    trigger_price=sell_trigger,
                    quantity=sell_quantity,
                    stop_price=risk_line,
                    recovery_price=recovery_line,
                    lot_size=lot_size,
                    summary=f"{_number(sell_trigger)} 附近卖 {_share_quantity(sell_quantity)}，只动机动仓。",
                    guardrail_text=f"只挂 {_number(sell_trigger)} 附近限价；未成交不追低卖，第二档触发前不加卖。",
                ),
                evidence=(cost_evidence, *_account_evidence(account, market_value), *factor_evidence),
                steps=(
                    TradeAdviceStep("第一档", _number(sell_trigger), f"卖 {_share_quantity(sell_quantity)}，只动机动仓。"),
                    TradeAdviceStep("第二档", _number(second_sell_trigger), "若继续上冲，再卖一档。"),
                    TradeAdviceStep("接回", _number(recovery_line), "卖出后回落到这里再回补。"),
                    TradeAdviceStep("失效", _number(risk_line), "跌破后转风控逻辑。"),
                ),
                strategy_status=strategy_status,
                strategy_score=score,
                strategy_rules=QLIB_FACTOR_SCORE_RULES,
                backtests=backtests,
                position=position_payload,
                account=account_payload,
                source="trade_advice.v1",
            )
        return TradeAdviceResult(
            market=market,
            symbol=symbol,
            name=name,
            action="hold",
            action_text="持有",
            headline=f"持有 {_share_quantity(quantity)}，当前不卖。",
            primary_order="今天不挂主动卖单。",
            next_trigger=f"{_number(sell_trigger)} 起卖第一档机动仓 {_share_quantity(sell_quantity)}；{_number(second_sell_trigger)} 以上再看第二档。",
            risk_line=f"{_number(risk_line)} 有效跌破才进入风控减仓。",
            recovery_line=f"{_number(recovery_line)} 附近可接回前面卖出的机动仓。",
            operation=_operation_payload(
                intent="watch_sell",
                side="sell",
                order_type="conditional_limit",
                price=sell_trigger,
                trigger_price=sell_trigger,
                quantity=sell_quantity,
                stop_price=risk_line,
                recovery_price=recovery_line,
                lot_size=lot_size,
                summary=f"暂不卖；到 {_number(sell_trigger)} 起卖 {_share_quantity(sell_quantity)}。",
                guardrail_text=f"未到 {_number(sell_trigger)} 不卖；跌破 {_number(risk_line)} 才转风控减仓。",
            ),
            evidence=(cost_evidence, *_account_evidence(account, market_value), *factor_evidence),
            steps=(
                TradeAdviceStep("现在", "不卖", "没有过热卖出或风控减仓信号。"),
                TradeAdviceStep("第一档", _number(sell_trigger), f"卖 {_share_quantity(sell_quantity)}，只动机动仓。"),
                TradeAdviceStep("第二档", _number(second_sell_trigger), "若继续过热，再卖一档。"),
                TradeAdviceStep("接回", _number(recovery_line), "卖出后回落到这里可回补。"),
            ),
            strategy_status=strategy_status,
            strategy_score=score,
            strategy_rules=QLIB_FACTOR_SCORE_RULES,
            backtests=backtests,
            position=position_payload,
            account=account_payload,
            source="trade_advice.v1",
        )

    if score is not None and score >= 70 and return20 is not None and return20 > 0:
        first_lot_budget = _suggested_first_lot_budget(account, fallback_budget=5000)
        buy_quantity = _round_to_lot(first_lot_budget / price, lot_size)
        return TradeAdviceResult(
            market=market,
            symbol=symbol,
            name=name,
            action="buy",
            action_text="可买首仓",
            headline="没有持仓，当前满足首仓观察条件。",
            primary_order=f"首仓可买 {_share_quantity(buy_quantity)}，不要一次打满。",
            next_trigger=f"站稳 {_number(_round_price(price * 1.02))} 后再考虑加一档。",
            risk_line=f"{_number(risk_line)} 跌破则放弃首仓计划。",
            recovery_line=f"{_number(_round_price(price * 0.97))} 附近更适合低吸。",
            operation=_operation_payload(
                intent="buy",
                side="buy",
                order_type="limit",
                price=price,
                trigger_price=price,
                quantity=buy_quantity,
                stop_price=risk_line,
                recovery_price=_round_price(price * 0.97),
                lot_size=lot_size,
                summary=f"首仓买 {_share_quantity(buy_quantity)}，预算约 {_currency(buy_quantity * price if buy_quantity else None)}。",
                guardrail_text="只做首仓试错；未站稳加仓触发价前不加仓，跌破风控线撤销计划。",
            ),
            evidence=(*_account_evidence(account, None), *factor_evidence),
            steps=(
                TradeAdviceStep("首仓", _share_quantity(buy_quantity), "控制试错成本。"),
                TradeAdviceStep("加仓", _number(_round_price(price * 1.02)), "确认走势后再加。"),
                TradeAdviceStep("失效", _number(risk_line), "跌破不追。"),
            ),
            strategy_status=strategy_status,
            strategy_score=score,
            strategy_rules=QLIB_FACTOR_SCORE_RULES,
            backtests=backtests,
            position=position_payload,
            account=account_payload,
            source="trade_advice.v1",
        )

    return TradeAdviceResult(
        market=market,
        symbol=symbol,
        name=name,
        action="buy_watch",
        action_text="等待",
        headline="没有持仓，当前先不买。",
        primary_order="等待策略分转强或价格回到更好的买点。",
        next_trigger=f"{_number(_round_price(price * 1.03))} 上方转强后再评估首仓。",
        risk_line=f"{_number(risk_line)} 跌破说明形态转弱。",
        recovery_line=f"{_number(_round_price(price * 0.97))} 附近重新计算低吸性价比。",
        operation=_operation_payload(
            intent="watch_buy",
            side="buy",
            order_type="conditional",
            trigger_price=_round_price(price * 1.03),
            stop_price=risk_line,
            recovery_price=_round_price(price * 0.97),
            lot_size=lot_size,
            summary=f"暂不买；{_number(_round_price(price * 1.03))} 上方转强后再评估首仓。",
            guardrail_text="没有胜率优势时不追买；转强或回落到低吸区后重新计算。",
        ),
        evidence=(*_account_evidence(account, None), *factor_evidence),
        steps=(
            TradeAdviceStep("现在", "不买", "没有足够胜率优势。"),
            TradeAdviceStep("转强", _number(_round_price(price * 1.03)), "再看首仓。"),
            TradeAdviceStep("低吸", _number(_round_price(price * 0.97)), "回落后重新评估。"),
        ),
        strategy_status=strategy_status,
        strategy_score=score,
        strategy_rules=QLIB_FACTOR_SCORE_RULES,
        backtests=backtests,
        position=position_payload,
        account=account_payload,
        source="trade_advice.v1",
    )


def serialize_trade_advice_result(result: TradeAdviceResult) -> dict[str, Any]:
    event_evidence = result.event_evidence or build_trade_event_evidence(
        market=result.market,
        symbol=result.symbol,
        name=result.name,
    )
    return {
        "market": result.market,
        "symbol": result.symbol,
        "name": result.name,
        "action": result.action,
        "action_text": result.action_text,
        "headline": result.headline,
        "primary_order": result.primary_order,
        "next_trigger": result.next_trigger,
        "risk_line": result.risk_line,
        "recovery_line": result.recovery_line,
        "operation": result.operation,
        "evidence": list(result.evidence),
        "steps": [
            {"label": step.label, "value": step.value, "note": step.note}
            for step in result.steps
        ],
        "event_evidence": [serialize_trade_event_evidence(item) for item in event_evidence],
        "strategy_status": result.strategy_status,
        "strategy_score": result.strategy_score,
        "strategy_rules": list(result.strategy_rules),
        "backtests": [
            {
                "strategy_id": item.strategy_id,
                "strategy_name": item.strategy_name,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "total_return_percent": item.total_return_percent,
                "benchmark_name": item.benchmark_name,
                "benchmark_return_percent": item.benchmark_return_percent,
                "excess_return_percent": item.excess_return_percent,
                "trade_count": item.trade_count,
                "summary": item.summary,
                "error": item.error,
            }
            for item in result.backtests
        ],
        "position": result.position,
        "account": result.account,
        "source": result.source,
    }


def build_trade_candidate_advice(
    analysis: QlibFactorAnalysis,
    *,
    has_position: bool = False,
    position_quantity: float | None = None,
    backtests: tuple[TradeAdviceBacktestEvidence, ...] = (),
    budget: float = 5000,
    account: AccountTradingContext | None = None,
) -> TradeCandidateAdvice:
    market = _normalize_market(analysis.market)
    symbol = _normalize_symbol(analysis.symbol)
    price = _number_or_none(analysis.latest_close)
    score = analysis.score
    return20 = analysis.return_20
    return60 = analysis.return_60
    ma20_distance = analysis.ma_20_distance
    volume_ratio = analysis.volume_ratio_5_20
    lot_size = 200 if market == "HK" else 100
    effective_budget = _suggested_first_lot_budget(account, fallback_budget=budget)
    affordable_quantity = (effective_budget / price) if price else 0
    buy_quantity = _round_to_lot(affordable_quantity, lot_size) if affordable_quantity >= lot_size else 0
    account_payload = _account_payload(account, position_market_value=None, first_lot_budget=effective_budget)
    risk_base = analysis.ma_60 * 0.98 if analysis.ma_60 is not None else (price * 0.92 if price else 0)
    risk_line = _round_price(min(risk_base, price * 0.92) if price else risk_base)
    next_trigger = _round_price(price * 1.02) if price else 0
    rank_score = _candidate_rank_score(
        score=score,
        return20=return20,
        return60=return60,
        ma20_distance=ma20_distance,
        volume_ratio=volume_ratio,
        has_position=has_position,
    )
    evidence = (
        f"综合策略分 {score if score is not None else '-'}，信号：{analysis.signal or '等待评分'}。",
        f"20日 {_signed_percent(return20)}，60日 {_signed_percent(return60)}。",
        "20日线位置暂不可用。" if ma20_distance is None else f"相对20日线 {_signed_percent(ma20_distance)}。",
        "量能暂不可用。" if volume_ratio is None else f"5/20日量能比 {_ratio(volume_ratio)}。",
    )

    if has_position:
        quantity_text = _share_quantity(position_quantity)
        return TradeCandidateAdvice(
            market=market,
            symbol=symbol,
            name=analysis.name,
            action="hold",
            action_text="已有持仓",
            headline=f"已有 {quantity_text}，不作为新买候选。",
            primary_order="先走持仓维护逻辑，不重复开新仓。",
            next_trigger="-",
            risk_line="-",
            operation=_operation_payload(
                intent="none",
                side="none",
                order_type="none",
                summary="先走持仓维护逻辑，不重复开新仓。",
                lot_size=lot_size,
                guardrail_text="已有持仓时不在候选池重复开新仓。",
            ),
            evidence=evidence,
            steps=(
                TradeAdviceStep("现在", "不买", "已有持仓，交给持仓维护逻辑。"),
            ),
            strategy_score=score,
            backtests=backtests,
            current_price=price,
            rank_score=rank_score,
            account=account_payload,
            source="trade_candidate.v1",
        )

    if price is None or price <= 0 or score is None:
        return TradeCandidateAdvice(
            market=market,
            symbol=symbol,
            name=analysis.name,
            action="no_data",
            action_text="数据不足",
            headline="行情或评分不足，暂不推荐。",
            primary_order="等待日线数据补齐。",
            next_trigger="-",
            risk_line="-",
            operation=_operation_payload(
                intent="none",
                side="none",
                order_type="none",
                summary="等待日线数据补齐。",
                lot_size=lot_size,
                guardrail_text="行情或评分不完整时不下单。",
            ),
            evidence=evidence,
            steps=(),
            strategy_score=score,
            backtests=backtests,
            current_price=price,
            rank_score=rank_score,
            account=account_payload,
            source="trade_candidate.v1",
        )

    if buy_quantity <= 0:
        return TradeCandidateAdvice(
            market=market,
            symbol=symbol,
            name=analysis.name,
            action="buy_watch",
            action_text="现金不足",
            headline=f"{analysis.name} 信号可以观察，但当前首仓预算不足一手。",
            primary_order="先不买，等待可用现金或降低候选优先级。",
            next_trigger=f"当前建议预算 {_currency(effective_budget)}；可用现金覆盖一手后再评估。",
            risk_line=f"{_number(risk_line)} 跌破说明形态转弱。",
            operation=_operation_payload(
                intent="watch_buy",
                side="buy",
                order_type="budget_wait",
                cash_budget=effective_budget,
                stop_price=risk_line,
                lot_size=lot_size,
                summary=f"首仓预算 {_currency(effective_budget)} 不足一手，先不买。",
                guardrail_text="预算不足一手时不拆零追买；等现金覆盖一手后再评估。",
            ),
            evidence=(*_account_evidence(account, None), *evidence),
            steps=(
                TradeAdviceStep("现在", "不买", "首仓预算不足一手。"),
                TradeAdviceStep("资金", _currency(effective_budget), "现金覆盖一手后再评估。"),
                TradeAdviceStep("失效", _number(risk_line), "跌破后移出首仓计划。"),
            ),
            strategy_score=score,
            backtests=backtests,
            current_price=price,
            rank_score=rank_score,
            account=account_payload,
            source="trade_candidate.v1",
        )

    if score >= 70 and return20 is not None and return20 > 0:
        return TradeCandidateAdvice(
            market=market,
            symbol=symbol,
            name=analysis.name,
            action="buy",
            action_text="可买首仓",
            headline=f"{analysis.name} 满足首仓候选条件。",
            primary_order=f"首仓约 {_share_quantity(buy_quantity)}，预算约 {_currency(buy_quantity * price if buy_quantity else None)}。",
            next_trigger=f"站稳 {_number(next_trigger)} 后再加一档。",
            risk_line=f"{_number(risk_line)} 跌破则撤销首仓计划。",
            operation=_operation_payload(
                intent="buy",
                side="buy",
                order_type="limit",
                price=price,
                trigger_price=price,
                quantity=buy_quantity,
                cash_budget=effective_budget,
                stop_price=risk_line,
                lot_size=lot_size,
                summary=f"首仓买 {_share_quantity(buy_quantity)}，预算约 {_currency(buy_quantity * price if buy_quantity else None)}。",
                guardrail_text="只做首仓，不一次打满；跌破风控线撤销首仓计划。",
            ),
            evidence=(*_account_evidence(account, None), *evidence),
            steps=(
                TradeAdviceStep("首仓", _share_quantity(buy_quantity), "只做首仓试错，不一次打满。"),
                TradeAdviceStep("加仓", _number(next_trigger), "站稳后再加一档。"),
                TradeAdviceStep("失效", _number(risk_line), "跌破后撤销首仓计划。"),
            ),
            strategy_score=score,
            backtests=backtests,
            current_price=price,
            rank_score=rank_score,
            account=account_payload,
            source="trade_candidate.v1",
        )

    return TradeCandidateAdvice(
        market=market,
        symbol=symbol,
        name=analysis.name,
        action="buy_watch",
        action_text="等待",
        headline=f"{analysis.name} 先列入观察，不直接买。",
        primary_order="等待策略分转强、20日动量修复或价格回到更好位置。",
        next_trigger=f"{_number(_round_price(price * 1.03))} 上方转强后再评估首仓。",
        risk_line=f"{_number(risk_line)} 跌破说明形态转弱。",
        operation=_operation_payload(
            intent="watch_buy",
            side="buy",
            order_type="conditional",
            trigger_price=_round_price(price * 1.03),
            stop_price=risk_line,
            lot_size=lot_size,
            summary=f"暂不买；{_number(_round_price(price * 1.03))} 上方转强后再评估首仓。",
            guardrail_text="等待转强或更好价格，不追弱信号。",
        ),
        evidence=(*_account_evidence(account, None), *evidence),
        steps=(
            TradeAdviceStep("现在", "不买", "还没有足够胜率优势。"),
            TradeAdviceStep("转强", _number(_round_price(price * 1.03)), "再评估首仓。"),
            TradeAdviceStep("失效", _number(risk_line), "跌破后降低候选优先级。"),
        ),
        strategy_score=score,
        backtests=backtests,
        current_price=price,
        rank_score=rank_score,
        account=account_payload,
        source="trade_candidate.v1",
    )


def serialize_trade_candidate_advice(item: TradeCandidateAdvice) -> dict[str, Any]:
    event_evidence = item.event_evidence or build_trade_event_evidence(
        market=item.market,
        symbol=item.symbol,
        name=item.name,
    )
    return {
        "market": item.market,
        "symbol": item.symbol,
        "name": item.name,
        "action": item.action,
        "action_text": item.action_text,
        "headline": item.headline,
        "primary_order": item.primary_order,
        "next_trigger": item.next_trigger,
        "risk_line": item.risk_line,
        "operation": item.operation,
        "evidence": list(item.evidence),
        "steps": [
            {"label": step.label, "value": step.value, "note": step.note}
            for step in item.steps
        ],
        "event_evidence": [serialize_trade_event_evidence(event) for event in event_evidence],
        "strategy_score": item.strategy_score,
        "backtests": [
            {
                "strategy_id": backtest.strategy_id,
                "strategy_name": backtest.strategy_name,
                "start_date": backtest.start_date,
                "end_date": backtest.end_date,
                "total_return_percent": backtest.total_return_percent,
                "benchmark_name": backtest.benchmark_name,
                "benchmark_return_percent": backtest.benchmark_return_percent,
                "excess_return_percent": backtest.excess_return_percent,
                "trade_count": backtest.trade_count,
                "summary": backtest.summary,
                "error": backtest.error,
            }
            for backtest in item.backtests
        ],
        "current_price": item.current_price,
        "rank_score": item.rank_score,
        "account": item.account,
        "source": item.source,
    }


def build_backtest_evidence(
    *,
    strategy_id: str,
    strategy_name: str,
    start_date: str,
    end_date: str,
    total_return_percent: float | None,
    benchmark_name: str,
    benchmark_return_percent: float | None,
    trade_count: int,
    error: str = "",
) -> TradeAdviceBacktestEvidence:
    excess = (
        total_return_percent - benchmark_return_percent
        if total_return_percent is not None and benchmark_return_percent is not None
        else None
    )
    if error:
        summary = f"{strategy_name} 回测暂不可用：{error}"
    else:
        benchmark_text = f"，{benchmark_name} {_signed_percent(benchmark_return_percent)}" if benchmark_return_percent is not None else ""
        excess_text = f"，超额 {_signed_percent(excess)}" if excess is not None else ""
        summary = f"{start_date} 至 {end_date}：策略 {_signed_percent(total_return_percent)}{benchmark_text}{excess_text}，交易 {trade_count} 次。"
    return TradeAdviceBacktestEvidence(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        start_date=start_date,
        end_date=end_date,
        total_return_percent=total_return_percent,
        benchmark_name=benchmark_name,
        benchmark_return_percent=benchmark_return_percent,
        excess_return_percent=excess,
        trade_count=trade_count,
        summary=summary,
        error=error,
    )


def serialize_trade_event_evidence(item: TradeEventEvidence) -> dict[str, str]:
    return {
        "title": item.title,
        "event_date": item.event_date,
        "source": item.source,
        "url": item.url,
        "impact": item.impact,
        "summary": item.summary,
    }


def build_trade_event_evidence(*, market: str, symbol: str, name: str) -> tuple[TradeEventEvidence, ...]:
    normalized_symbol = _normalize_symbol(symbol)
    text = f"{market}.{normalized_symbol} {name}".lower()
    is_robot_theme = normalized_symbol in {"562500", "159278"} or "机器人" in name or "robot" in text
    if not is_robot_theme:
        return ()
    return (
        TradeEventEvidence(
            title="人形机器人产业进入规模化落地叙事",
            event_date="2026-01-08",
            source="新华网/中国证券报",
            url="https://www.news.cn/tech/20260108/46b1220e159d4f80bc6a4240eb3b47b5/c.html",
            impact="support",
            summary="行业主线从技术展示转向成本下探、场景拓展和规模放量，支持机器人主题的中期景气依据。",
        ),
        TradeEventEvidence(
            title="具身智能被纳入未来产业政策表述",
            event_date="2026-03-20",
            source="中国电子学会",
            url="https://www.cie.org.cn/list_43/15791.html",
            impact="support",
            summary="2026年政府工作报告提出打造智能经济新形态，培育具身智能等未来产业，提供政策侧支撑。",
        ),
        TradeEventEvidence(
            title="美国国防部名单扩大到部分中国机器人相关企业",
            event_date="2026-06-09",
            source="The Guardian",
            url="https://www.theguardian.com/world/2026/jun/09/china-military-tech-companies-byd-alibaba-baidu-pentagon-claims",
            impact="risk",
            summary="外部限制本身不直接等于行业走弱，但会提高供应链、融资和估值波动风险，适合降低追高冲动。",
        ),
    )


def _candidate_rank_score(
    *,
    score: int | None,
    return20: float | None,
    return60: float | None,
    ma20_distance: float | None,
    volume_ratio: float | None,
    has_position: bool,
) -> float:
    rank = float(score or 0)
    if return20 is not None:
        rank += min(max(return20, -20), 30) * 0.8
    if return60 is not None:
        rank += min(max(return60, -30), 60) * 0.35
    if ma20_distance is not None:
        if -8 <= ma20_distance <= 5:
            rank += 8
        elif ma20_distance > 12:
            rank -= 8
        elif ma20_distance < -15:
            rank -= 6
    if volume_ratio is not None and volume_ratio >= 1.4:
        rank += 5 if (return20 or 0) > 0 else -5
    if has_position:
        rank -= 200
    return round(rank, 2)


def position_input_from_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    market: str,
    symbol: str,
    name: str,
    current_price: float | None = None,
) -> StockPositionInput:
    if snapshot is None:
        return StockPositionInput(
            market=market,
            symbol=symbol,
            name=name,
            current_price=current_price,
        )
    return StockPositionInput(
        market=str(snapshot.get("market") or market),
        symbol=str(snapshot.get("security_code") or symbol),
        name=str(snapshot.get("security_name") or name),
        quantity=_number_or_none(snapshot.get("quantity")),
        cost_price=_number_or_none(snapshot.get("cost_price")),
        current_price=_first_number(snapshot.get("current_price"), current_price),
        market_value=_number_or_none(snapshot.get("market_value")),
    )


def account_context_from_snapshot(snapshot: dict[str, Any] | None) -> AccountTradingContext:
    if snapshot is None:
        return AccountTradingContext()
    return AccountTradingContext(
        total_asset=_number_or_none(snapshot.get("total_asset")),
        cash_available=_first_number(snapshot.get("cash_available"), snapshot.get("cash_balance")),
    )


def _position_payload(
    *,
    quantity: float,
    cost_price: float | None,
    current_price: float | None,
    market_value: float | None,
) -> dict[str, Any]:
    return {
        "quantity": quantity,
        "cost_price": cost_price,
        "current_price": current_price,
        "market_value": market_value,
        "quantity_text": _share_quantity(quantity) if quantity > 0 else "无持仓",
        "cost_price_text": _number(cost_price),
        "current_price_text": _number(current_price),
        "market_value_text": _currency(market_value),
    }


def _account_payload(
    account: AccountTradingContext | None,
    *,
    position_market_value: float | None,
    first_lot_budget: float | None = None,
) -> dict[str, Any]:
    if account is None:
        return {
            "total_asset": None,
            "cash_available": None,
            "position_weight_percent": None,
            "max_single_position_percent": None,
            "first_lot_budget": first_lot_budget,
            "summary": "未读取到账户资金约束。",
        }
    weight = _position_weight(account, position_market_value)
    max_percent = account.max_single_position_percent * 100
    parts = []
    if account.total_asset is not None:
        parts.append(f"总资产 {_currency(account.total_asset)}")
    if account.cash_available is not None:
        parts.append(f"可用现金 {_currency(account.cash_available)}")
    if weight is not None:
        parts.append(f"当前仓位 {_signed_percent(weight)}")
    parts.append(f"单票上限 {max_percent:.0f}%")
    return {
        "total_asset": account.total_asset,
        "cash_available": account.cash_available,
        "position_weight_percent": weight,
        "max_single_position_percent": max_percent,
        "first_lot_budget": first_lot_budget,
        "summary": "，".join(parts),
    }


def _account_evidence(account: AccountTradingContext | None, position_market_value: float | None) -> tuple[str, ...]:
    if account is None or (account.total_asset is None and account.cash_available is None):
        return ("账户资金约束暂不可用，买入预算按默认首仓试算。",)
    weight = _position_weight(account, position_market_value)
    evidence = [f"账户约束：{_account_payload(account, position_market_value=position_market_value)['summary']}。"]
    if weight is not None and weight > account.max_single_position_percent * 100:
        evidence.append(f"该标的仓位 {_signed_percent(weight)}，高于单票上限 {account.max_single_position_percent * 100:.0f}%，上涨触发卖档应优先降集中度。")
    return tuple(evidence)


def _position_weight(account: AccountTradingContext | None, market_value: float | None) -> float | None:
    if account is None or account.total_asset is None or not market_value:
        return None
    if account.total_asset <= 0:
        return None
    return market_value / account.total_asset * 100


def _suggested_first_lot_budget(account: AccountTradingContext | None, *, fallback_budget: float) -> float:
    if account is None:
        return fallback_budget
    budgets = [fallback_budget, account.max_first_lot_budget]
    if account.cash_available is not None:
        budgets.append(max(0, account.cash_available * account.first_lot_cash_percent))
    if account.total_asset is not None:
        budgets.append(max(0, account.total_asset * account.first_lot_asset_percent))
    return min(budgets)


def _planned_sell_quantity(quantity: float, lot_size: int) -> float:
    if quantity <= 0:
        return 0
    planned = min(quantity * 0.22, max(quantity - quantity * 0.65, 0))
    rounded = _round_to_lot(planned, lot_size)
    return rounded if rounded > 0 else _round_to_lot(quantity * 0.2, lot_size)


def _operation_payload(
    *,
    intent: str,
    side: str,
    order_type: str,
    summary: str,
    lot_size: int,
    price: float | None = None,
    trigger_price: float | None = None,
    quantity: float | None = None,
    cash_budget: float | None = None,
    stop_price: float | None = None,
    recovery_price: float | None = None,
    guardrail_text: str = "",
) -> dict[str, Any]:
    normalized_quantity = _number_or_zero(quantity)
    amount = normalized_quantity * price if normalized_quantity > 0 and price is not None else None
    return {
        "intent": intent,
        "side": side,
        "order_type": order_type,
        "price": price,
        "price_text": _number(price),
        "trigger_price": trigger_price,
        "trigger_price_text": _number(trigger_price),
        "quantity": normalized_quantity,
        "quantity_text": _share_quantity(normalized_quantity),
        "amount": amount,
        "amount_text": _currency(amount),
        "cash_budget": cash_budget,
        "cash_budget_text": _currency(cash_budget),
        "stop_price": stop_price,
        "stop_price_text": _number(stop_price),
        "recovery_price": recovery_price,
        "recovery_price_text": _number(recovery_price),
        "lot_size": lot_size,
        "summary": summary,
        "guardrail_text": guardrail_text,
    }


def _normalize_market(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"SHA", "SSE"}:
        return "SH"
    if text in {"SZA", "SZSE"}:
        return "SZ"
    if text == "HKG":
        return "HK"
    return text


def _normalize_symbol(value: object) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits and len(digits) <= 6 else text


def _number_or_none(value: object) -> float | None:
    if isinstance(value, int | float):
        number = float(value)
        return number if number == number else None
    text = str(value or "").replace(",", "").replace("，", "").replace("%", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _number_or_zero(value: object) -> float:
    return _number_or_none(value) or 0


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number_or_none(value)
        if number is not None:
            return number
    return None


def _round_to_lot(value: float, lot_size: int) -> float:
    if value <= 0:
        return 0
    return max(lot_size, int(value // lot_size) * lot_size)


def _round_price(value: float) -> float:
    if value <= 0:
        return 0
    return round(value, 3)


def _number(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _currency(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def _share_quantity(value: float | None) -> str:
    if not value:
        return "-"
    return f"{round(value):,} 份"


def _signed_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def _ratio(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}x"


def _nullable_percent_text(label: str, value: float | None) -> str:
    return f"{label}--" if value is None else f"{label}{_signed_percent(value)}"
