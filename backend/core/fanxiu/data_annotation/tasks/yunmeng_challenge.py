from __future__ import annotations

"""云梦试剑按当日可用次数补齐论剑玉（只挑战，不兑换）。

兑换宝阁每一档目标都靠论剑玉支撑：统一玩法榜原先只把云梦的「收尾兑换」接进
生产 checkpoint，活动期间既没有作业打开页面采集事实，也没有人真的去挑战。
本模块把「先刷新事实、再用当日可用次数挑战」接成一次机会式作业：

1. 打开云梦主页/榜单/兑换宝阁，刷新宝阁、钱包与榜单事实（兑换预算门禁由此就绪）；
2. 用兑换计划缺口 + 活动证据里的实测收益率（最近相邻批次的
   ``currency_delta / requested_challenges``）估算所需挑战次数，夹到 105% 再执行，
   绝不再把「面板上限（含全部论剑令）」当目标；
3. 只挑战，不购买、不兑换；不使用论剑令补体力（补体力是需要单独授权的资源消耗）。

缺口为 0 时直接不挑战；当日次数不够时如实返回未达成与缺口，不做跨期补打、
不提前消费。
"""

from dataclasses import dataclass
from datetime import datetime
import math
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.exchange_planning import (
    ExchangeMeasurement,
    calculate_exchange_currency_gap,
    estimate_remaining_attempts,
    latest_exchange_yield_rate,
)
from backend.db import engine


@dataclass(frozen=True)
class _AttemptBudget:
    """Challenge sizing derived from the closing target gap and measured yield.

    ``attempt_budget is None`` means no explicit cap could be proven: either the
    gap is already zero (``required_new_currency == 0``) or no measured yield
    sample exists yet, in which case the native driver keeps probing safely.
    """

    required_new_currency: int
    attempt_budget: int | None
    yield_sample_count: int


def _estimate_challenge_attempt_budget(
    detail: Any,
    batches: list[dict[str, Any]],
) -> _AttemptBudget | None:
    """Estimate the attempts needed for the closing-goods gap, capped at 105%.

    Returns ``None`` when the detail carries no usable closing-goods budget, so
    the caller must keep the existing native all-or-nothing validation instead
    of guessing.  Yield is the latest adjacent batch ratio
    ``currency_delta / requested_challenges`` taken from the activity evidence.
    """

    # 只在钱包与宝阁事实同窗口新鲜时按缺口算次数；否则交回原生执行器的
    # freshness 门禁 fail-closed，绝不用陈旧余额估算出过大的挑战预算。
    if not bool(getattr(detail, "budget_ready", True)):
        return None
    plan = dict(getattr(detail, "exchange_plan", None) or {})
    target_budgets = dict(plan.get("target_budgets") or {})
    closing = dict(target_budgets.get("收尾道具") or {})
    target_total = int(closing.get("target_total_tokens") or 0)
    target_remaining = int(closing.get("target_remaining_tokens") or 0)
    if target_total <= 0 or target_remaining < 0:
        return None
    gap = calculate_exchange_currency_gap(
        target_total_tokens=target_total,
        target_remaining_tokens=target_remaining,
        current_currency=int(getattr(detail, "current_currency", 0) or 0),
        cumulative_currency=int(getattr(detail, "cumulative_currency", 0) or 0),
    )
    measurements: list[ExchangeMeasurement] = []
    valid_batches = [
        batch
        for batch in batches
        if isinstance(batch, dict) and int(batch.get("requested_challenges") or 0) > 0
    ]
    if valid_batches:
        # The first ``currency_before`` is the baseline; each later cumulative
        # ``currency_after`` with its requested count forms one adjacent sample.
        measurements.append(
            ExchangeMeasurement(
                exchange_currency=int(valid_batches[0].get("currency_before") or 0),
                attempt_count_delta=None,
            )
        )
        for batch in valid_batches:
            measurements.append(
                ExchangeMeasurement(
                    exchange_currency=int(batch.get("currency_after") or 0),
                    attempt_count_delta=int(batch.get("requested_challenges") or 0),
                )
            )
    yield_rate = latest_exchange_yield_rate(measurements)
    if gap.required_new_currency == 0:
        return _AttemptBudget(0, None, 0 if yield_rate is None else 1)
    if yield_rate is None:
        return _AttemptBudget(gap.required_new_currency, None, 0)
    needed = estimate_remaining_attempts(
        accumulated_exchange_currency=0,
        target_exchange_currency=gap.required_new_currency,
        yield_rate=yield_rate,
    )
    budget = max(1, math.ceil(int(needed or 0) * 1.05))
    return _AttemptBudget(gap.required_new_currency, budget, 1)


def execute_yunmeng_challenge_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    captured_at: datetime,
    required_fact_watermark: datetime,
) -> Iterator[Any]:
    """刷新云梦事实后，用当日可用次数把论剑玉推进到目标档次。"""

    del required_fact_watermark
    from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
    from backend.core.fanxiu.activity.yunmeng_exchange import (
        collect_and_store_yunmeng_exchange_activity,
    )
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_active import (
        load_yunmeng_pages,
    )
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_native_auto import (
        execute_yunmeng_native_auto_job,
    )

    label = "云梦_自动挑战"
    with Session(engine) as session:
        activity = seed_ranking_occurrence(
            session,
            occurrence,
            captured_at=captured_at.isoformat(timespec="seconds"),
        )
        activity_id = str(activity.id)
        session.commit()

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    # 事实先就绪：兑换宝阁与钱包是预算门禁，榜单是档次奖励依据。
    yield from load_yunmeng_pages(context, occurrence, label=label)
    from backend.models import FanxiuExchangeActivity

    with Session(engine) as session:
        detail = collect_and_store_yunmeng_exchange_activity(
            session,
            activity_id=activity_id,
            collect_runtime_shop=True,
        )
        activity_row = session.get(FanxiuExchangeActivity, activity_id)
        batches = list(
            dict((activity_row.evidence if activity_row is not None else {}) or {}).get(
                "yunmeng_native_auto_batches"
            )
            or []
        )
        session.commit()
    # 目标不再取「面板上限（含全部论剑令）」：先用兑换计划缺口和证据里的实测
    # 收益率算出所需次数，再夹到 105%。缺口为 0 时直接不挑战，避免溢出。
    attempt_budget = _estimate_challenge_attempt_budget(detail, batches)
    yield from context.go_scene(34)
    if attempt_budget is not None and attempt_budget.required_new_currency == 0:
        return {
            "result": "success",
            "message": "云梦试剑收尾道具预算已满足，无需自动挑战",
            "activity_id": activity_id,
            "target_reached": True,
            "requested_challenges": 0,
            "currency_after": int(getattr(detail, "current_currency", 0) or 0),
            "cumulative_after": int(getattr(detail, "cumulative_currency", 0) or 0),
            "required_new_currency": 0,
            "skip_reason": "closing_goods_gap_zero",
            "checkpoint": "yunmeng_challenge",
            "target_level": "收尾道具",
        }

    options: dict[str, Any] = {
        "use_available_attempts": True,
        "max_batches": max(1, int(payload.get("yunmeng_max_batches") or 20)),
        "terminal_polls": max(1, int(payload.get("yunmeng_terminal_polls") or 1800)),
    }
    batch_limit = int(payload.get("yunmeng_challenge_batch") or 0)
    if batch_limit > 0:
        options["requested_challenges"] = batch_limit
    elif attempt_budget is not None and attempt_budget.attempt_budget:
        options["requested_challenges"] = attempt_budget.attempt_budget
    # 论剑令补体力是不可逆消耗：只有在作业 payload 里显式授权时才开启。
    if bool(payload.get("yunmeng_use_refill_items", False)):
        options["use_refill_items"] = True
    result = yield from execute_yunmeng_native_auto_job(
        runner,
        ctx,
        options,
        stop_event,
    )
    if str(result.get("activity_id") or "") not in {"", activity_id}:
        raise RuntimeError(f"{label}：挑战结果切换到其他活动实例")
    return {
        **result,
        "checkpoint": "yunmeng_challenge",
        "target_level": (
            "收尾道具" if result.get("target_reached") else "当日可用次数内的最佳推进"
        ),
    }


__all__ = ["execute_yunmeng_challenge_checkpoint"]
