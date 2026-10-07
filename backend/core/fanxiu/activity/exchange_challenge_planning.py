"""Pure next-milestone planning; no purchasing, inventory opening or GUI calls."""
from collections.abc import Mapping, Sequence
from datetime import datetime
from fractions import Fraction
from math import ceil
from .exchange_planning import calculate_exchange_currency_gap, estimate_remaining_attempts, ExchangeYieldRate


GAMEPLAY_RANK_INITIAL_ATTEMPTS = 100


def required_exchange_resource_stock(*, attempts: int, free_points: int,
                                      points_per_item: int, points_per_attempt: Fraction) -> int:
    """Stock floor for one whole gameplay-rank batch, after free resources.

    Owner seq1482 authorizes shortage exchange for challenge and exploration
    resources. The adapter supplies verified identities and measured costs;
    this pure budget never authorizes cash/recharge or an unverified shop.
    Completed batches must claim task rewards before planning the next tier.
    """
    if (any(type(v) is not int for v in (attempts, free_points, points_per_item))
            or attempts <= 0 or free_points < 0 or points_per_item <= 0
            or not isinstance(points_per_attempt, Fraction) or points_per_attempt <= 0):
        raise ValueError('玩法榜补给缺少有效整轮次数及资源换算')
    shortage = max(0, ceil(attempts * points_per_attempt) - free_points)
    return (shortage + points_per_item - 1) // points_per_item


def parse_exchange_funding_deadline(value) -> datetime | None:
    """Optional owner deadline; it never changes the occurrence's final date."""
    if value is None:
        return None
    if not isinstance(value, (str, datetime)):
        raise ValueError('补币截止必须是带时区的ISO时间')
    try:
        deadline = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise ValueError('补币截止必须是带时区的ISO时间') from exc
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError('补币截止必须带时区')
    return deadline


def exchange_challenge_milestones(items: Sequence[Mapping]) -> list[dict]:
    """Use the same cumulative commodity thresholds shown by the exchange API.

    Locked rows retain their reservation. Purchased quantities reduce only
    remaining cost. Unlimited rows end the finite target chain.
    """
    ordered = sorted((r for r in items if r.get("priority_order") is not None),
                     key=lambda r: (r["priority_order"], r["source_order"]))
    result, remaining, previous = [], 0, 0
    seen = set()
    for row in ordered:
        goods_id = int(row["goods_id"])
        if goods_id <= 0 or goods_id in seen:
            raise ValueError("玩法榜商品身份无效或重复")
        seen.add(goods_id)
        limit = int(row["purchase_limit"])
        if limit < 0:
            break
        total = row.get("cumulative_tokens")
        if total is None or int(total) < previous:
            raise ValueError("玩法榜有限商品缺少一致的累计兑换额度")
        cost, bought = int(row["token_cost"]), int(row["purchased_count"])
        if cost < 0 or not 0 <= bought <= limit:
            raise ValueError("玩法榜商品成本或已购数量无效")
        remaining += cost * (limit - bought)
        previous = int(total)
        result.append({"goods_id": row["goods_id"], "name": row.get("name", ""),
                       "target_total_tokens": previous, "target_remaining_tokens": remaining})
    if not result:
        raise ValueError("玩法榜未采集到有限兑换档次")
    return result


def plan_exchange_challenge_batch(*, milestones, current_currency, cumulative_currency,
                                     samples, capacity, now: datetime, activity_end_at: datetime,
                                     batch_unit: int = 1, minimum_batch: int = 1,
                                     funding_deadline: datetime | None = None):
    """First 100, then exactly ceil(next gap / last completed batch yield).

    Insufficient capacity is a pass with a recorded deficit, never a smaller
    batch or a different target. All historical samples remain in the journal.
    For an N-day occurrence, its highest finite milestone is unlocked only on
    its final gameplay date (in the occurrence timezone), never its shop closing
    date. Initialization and natural-stamina clearing are separate obligations.
    An earlier funding deadline stops new batches without unlocking the last
    day's tier early. Already funded targets remain idempotently completed.
    """
    if now.tzinfo is None or activity_end_at.tzinfo is None:
        raise ValueError("玩法榜规划时间与活动结束时间必须带时区")
    funding_deadline = parse_exchange_funding_deadline(funding_deadline)
    if type(batch_unit) is not int or batch_unit < 1:
        raise ValueError("玩法榜原生批量单位必须是正整数")
    if type(minimum_batch) is not int or minimum_batch < 1:
        raise ValueError("玩法榜单批下限必须是正整数")
    if min(int(current_currency), int(cumulative_currency)) < 0:
        raise ValueError("玩法榜钱包余额或累计值不能为负数")
    if not milestones:
        raise ValueError("玩法榜缺少有限累计档次")
    capacity = max(0, int(capacity))
    base = {"count": 0, "capacity": capacity, "needed": 0, "deficit": 0}
    if now >= activity_end_at:
        return {**base, "status": "pass", "reason": "activity_ended"}
    target = None
    if not samples:
        needed, phase = GAMEPLAY_RANK_INITIAL_ATTEMPTS, "initialization"
        gap = None
    else:
        phase = "formal"
        for milestone in milestones:
            gap = calculate_exchange_currency_gap(
                target_total_tokens=milestone["target_total_tokens"],
                target_remaining_tokens=milestone["target_remaining_tokens"],
                current_currency=current_currency, cumulative_currency=cumulative_currency,
            ).required_new_currency
            if gap:
                target = milestone
                break
        if target is None:
            return {**base, "status": "completed", "reason": "all_milestones_funded"}
        if (target is milestones[-1]
                and now.astimezone(activity_end_at.tzinfo).date() < activity_end_at.date()):
            return {**base, "status": "deferred", "reason": "final_day_reserved",
                    "phase": phase, "target": target, "gap": gap,
                    "unlock_at": activity_end_at.replace(hour=0, minute=0, second=0,
                                                         microsecond=0).isoformat()}
        last = samples[-1]
        completed = int(last["completed_attempts"] if "completed_attempts" in last else last["completed_exorcisms"])
        delta = int(last["currency_delta"] if "currency_delta" in last else last["magic_crystal_delta"])
        requested = int(last.get("requested_attempts", last.get("requested_exorcisms", completed)))
        # Providers may supply a validated native terminal overrun bound;
        # absent that contract, exact completion remains mandatory.
        maximum = int(last.get("maximum_attempts", requested))
        if requested <= 0 or not requested <= completed <= maximum:
            raise ValueError("玩法榜最近批次不是完整批次")
        if delta <= 0:
            return {**base, "status": "pass", "reason": "no_positive_yield", "target": target, "gap": gap}
        needed = estimate_remaining_attempts(accumulated_exchange_currency=0,
            target_exchange_currency=gap, yield_rate=ExchangeYieldRate(delta, completed))
    if funding_deadline is not None and now >= funding_deadline:
        return {**base, "status": "pass", "reason": "funding_deadline_reached",
                "target": target, "gap": gap,
                "funding_deadline": funding_deadline.isoformat()}
    # A gameplay-specific floor trades bounded overrun for fewer setup cycles.
    # Apply before capacity admission, never exceed available stock silently.
    needed = ((max(needed, minimum_batch) + batch_unit - 1) // batch_unit) * batch_unit
    insufficient = needed > capacity
    return {**base, "status": "pass" if insufficient else "ready", "phase": phase,
            "reason": "resource_insufficient" if insufficient else "next_batch",
            "target": target, "gap": gap, "needed": needed,
            "count": 0 if insufficient else needed, "deficit": max(0, needed-capacity)}
