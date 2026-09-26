"""Pure next-milestone planning; no purchasing, inventory opening or GUI calls."""
from collections.abc import Mapping, Sequence
from .exchange_planning import calculate_exchange_currency_gap, estimate_remaining_attempts, ExchangeYieldRate


def exchange_challenge_milestones(items: Sequence[Mapping]) -> list[dict]:
    """Use the same cumulative commodity thresholds shown by the exchange API.

    Locked rows retain their reservation. Purchased quantities reduce only
    remaining cost. Unlimited rows end the finite target chain.
    """
    ordered = sorted((r for r in items if r.get("priority_order") is not None),
                     key=lambda r: (r["priority_order"], r["source_order"]))
    result, remaining, previous = [], 0, 0
    for row in ordered:
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
                                     samples, capacity):
    """First 100, then exactly ceil(next gap / last completed batch yield).

    Insufficient capacity is a pass with a recorded deficit, never a smaller
    batch or a different target. All historical samples remain in the journal.
    """
    capacity = max(0, int(capacity))
    base = {"count": 0, "capacity": capacity, "needed": 0, "deficit": 0}
    target = None
    if not samples:
        needed, phase = 100, "initialization"
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
        last = samples[-1]
        completed, delta = int(last["completed_exorcisms"]), int(last["magic_crystal_delta"])
        if completed <= 0 or completed != int(last.get("requested_exorcisms", completed)):
            raise ValueError("玩法榜最近批次不是完整批次")
        if delta <= 0:
            return {**base, "status": "pass", "reason": "no_positive_yield", "target": target, "gap": gap}
        needed = estimate_remaining_attempts(accumulated_exchange_currency=0,
            target_exchange_currency=gap, yield_rate=ExchangeYieldRate(delta, completed))
    insufficient = needed > capacity
    return {**base, "status": "pass" if insufficient else "ready", "phase": phase,
            "reason": "resource_insufficient" if insufficient else "next_batch",
            "target": target, "gap": gap, "needed": needed,
            "count": 0 if insufficient else needed, "deficit": max(0, needed-capacity)}
