from __future__ import annotations

"""Reusable fixed-point loop for draw activities with cumulative rewards."""

import time
from typing import Any, Callable


SnapshotReader = Callable[[], dict[str, Any]]
Action = Callable[[], dict[str, Any]]


def run_draw_claim_cycle(
    *,
    read_snapshot: SnapshotReader,
    draw_once: Action,
    close_draw_result: Action,
    claim_rewards: Action,
    max_rounds: int = 256,
    reward_settle_seconds: float = 2.0,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    """Draw, claim milestones, and continue until rewards cannot add more draws.

    ``draw_once`` is responsible for persisting the activity's before/after
    analytical point.  This loop deliberately treats the draw batch size as an
    observed delta instead of assuming single or ten-draw semantics.
    """

    initial_claim = claim_rewards()
    rounds: list[dict[str, Any]] = []
    previous_x: int | None = None
    for round_index in range(max(1, int(max_rounds))):
        state = read_snapshot()
        if not state.get("complete"):
            raise RuntimeError(str(state.get("reason") or "抽奖运行态数据不完整"))
        available_draws = int(state.get("available_draws") or 0)
        if available_draws <= 0:
            # A final idempotent claim closes the fixed point: a milestone may
            # itself award draw currency even when the wallet was empty.
            final_claim = claim_rewards()
            deadline = time.monotonic() + max(0.0, float(reward_settle_seconds))
            while True:
                final_state = read_snapshot()
                if not final_state.get("complete"):
                    raise RuntimeError(
                        str(final_state.get("reason") or "抽奖终态运行态数据不完整")
                    )
                if int(final_state.get("available_draws") or 0) > 0:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(max(0.05, float(poll_seconds)))
            if int(final_state.get("available_draws") or 0) > 0:
                continue
            if final_state.get("claimable"):
                raise RuntimeError("抽奖终态仍存在可领取累抽奖励")
            return {
                "result": "success",
                "round_count": len(rounds),
                "rounds": rounds,
                "initial_claim": initial_claim,
                "final_claim": final_claim,
                "final_state": {
                    "activity_id": int(final_state.get("activity_id") or 0),
                    "x": int(final_state.get("x") or 0),
                    "y": int(final_state.get("y") or 0),
                    "available_draws": 0,
                    "claimed_count": int(final_state.get("claimed_count") or 0),
                },
                "stop_reason": "draws_exhausted_and_rewards_claimed",
            }

        draw = draw_once()
        current_x = int((draw.get("after") or {}).get("x") or 0)
        if previous_x is not None and current_x <= previous_x:
            raise RuntimeError(
                f"抽奖累计次数没有单调增加：{previous_x} -> {current_x}"
            )
        previous_x = current_x
        close_result = close_draw_result()
        claim = claim_rewards()
        rounds.append(
            {
                "round": round_index + 1,
                "draw": draw,
                "close_result": close_result,
                "claim": claim,
            }
        )
    raise RuntimeError(f"抽奖循环超过安全轮次上限：{max_rounds}")


__all__ = ["run_draw_claim_cycle"]
