from __future__ import annotations

"""Kunlun Secret binding for the shared Bothdraw lottery workflow."""

import re
import time
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.kunlun_secret_lottery import (
    kunlun_week_instance_id,
    record_kunlun_lottery_point,
)
from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryGoal,
    LotteryMilestone,
    LotteryPolicy,
    decide_lottery_action,
)
from backend.core.fanxiu.data_annotation.tasks.bothdraw_lottery import (
    BothdrawLotterySpec,
    claim_bothdraw_cumulative_rewards,
    close_bothdraw_result,
    draw_bothdraw_once,
)
from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_navigation import (
    KUNLUN_MAIN_SCENE_ID,
    open_kunlun_tab,
    read_kunlun_page,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    read_bothdraw_cumulative_rewards_runtime,
    read_bothdraw_lottery_runtime,
)
from backend.core.fanxiu.instrumentation.bothdraw_toggle import (
    read_bothdraw_ten_draw_runtime,
)


KUNLUN_CUMULATIVE_REWARD_SHAPE = "累抽奖励"
KUNLUN_CUMULATIVE_REWARD_SLOTS = 4
KUNLUN_TEN_DRAW_TOGGLE_SHAPE = "鉴宝十次"
# Do not borrow Penglai #451.  Kunlun requires an independent result-page
# asset and a real click/return validation before consumptive draws are enabled.
KUNLUN_DRAW_RESULT_SCENE_ID: int | None = 544


@dataclass(frozen=True)
class KunlunDrawDecision:
    action: str
    reason: str
    expected_batch_size: int = 0
    target_threshold: int | None = None


def decide_kunlun_next_draw(
    snapshot: dict[str, Any],
    *,
    allow_single_draws: bool = True,
) -> KunlunDrawDecision:
    """Plan one bounded action whose objective is the first selected grand prize."""

    if not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "昆仑鉴宝状态不完整"))
    available = int(snapshot.get("available_draws") or 0)
    progress = int(snapshot.get("progress") or 0)
    capacity = snapshot.get("selected_big_capacity")
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity != 20:
        raise RuntimeError(f"昆仑自选大奖池容量异常：{capacity}")
    remaining = snapshot.get("selected_big_remaining")
    if not isinstance(remaining, int) or isinstance(remaining, bool):
        raise RuntimeError("昆仑自选大奖剩余数量不完整，拒绝抽奖")
    if remaining not in (19, 20):
        raise RuntimeError(f"昆仑自选大奖剩余数量异常：{remaining}")

    milestones = [
        LotteryMilestone(
            threshold=int(reward.get("threshold") or 0),
            reward_draws=_reward_item_count(
                str(reward.get("reward") or ""), int(snapshot.get("cost_type") or 0)
            ),
            state=str(reward.get("state") or ""),
            reward_id=int(reward.get("id") or 0) or None,
        )
        for reward in snapshot.get("rewards") or []
        if int(reward.get("threshold") or 0) > 0
    ]
    decision = decide_lottery_action(
        {
            **snapshot,
            "available_draws": available,
            "progress": progress,
            "hit_count": capacity - remaining,
        },
        policy=LotteryPolicy(
            goal=LotteryGoal("first_hit"),
            remainder_mode="single" if allow_single_draws else "defer",
            top_up_positive_refund_after_goal=True,
        ),
        milestones=milestones,
    )
    action_map = {
        "first_hit_reached": "stop_first_grand_prize",
        "terminal_remainder_deferred": "stop_single_draws_deferred",
        "draws_exhausted_before_target": "stop_exhausted",
    }
    if decision.action == "claim_rewards":
        action = "claim_rewards"
    elif decision.action == "draw":
        action = "ten_draw" if decision.draw_mode == "ten_draw" else "single_draw"
    else:
        action = action_map.get(str(decision.stop_reason), "stop_first_grand_prize")
    return KunlunDrawDecision(
        action,
        decision.reason,
        expected_batch_size=decision.expected_batch_size,
        target_threshold=decision.target_threshold,
    )


def _read_coherent_state() -> dict[str, Any]:
    lottery = read_bothdraw_lottery_runtime()
    resources = read_bothdraw_cumulative_rewards_runtime()
    if not lottery.get("complete"):
        raise RuntimeError(str(lottery.get("reason") or "昆仑大奖池状态不完整"))
    if not resources.get("complete"):
        raise RuntimeError(str(resources.get("reason") or "昆仑抽奖资源状态不完整"))
    if (
        int(lottery.get("activity_id") or 0)
        != int(resources.get("activity_id") or 0)
        or int(lottery.get("x") or 0) != int(resources.get("x") or 0)
    ):
        raise RuntimeError("昆仑大奖池与抽奖资源不属于同一运行态")
    for key in ("selected_big_capacity", "selected_big_remaining"):
        if lottery.get(key) != resources.get(key):
            raise RuntimeError(f"昆仑大奖池字段跨快照不一致：{key}")
    return {**resources, **lottery, "complete": True}


def ensure_kunlun_draw_mode(
    runtime: Any,
    *,
    ten_draw: bool,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    """Set the UI mode only after reading and then re-reading V_UseTenTimes."""

    lottery = read_bothdraw_lottery_runtime()
    if not lottery.get("complete"):
        raise RuntimeError(str(lottery.get("reason") or "昆仑活动身份不完整"))
    activity_id = int(lottery.get("activity_id") or 0)
    if activity_id <= 0:
        raise RuntimeError("昆仑活动身份无效")
    before = read_bothdraw_ten_draw_runtime(
        expected_activity_id=activity_id,
        expected_x=int(lottery.get("x") or 0),
        expected_y=int(lottery.get("y") or 0),
    )
    if not before.get("complete") or not isinstance(
        before.get("ten_draw_enabled"), bool
    ):
        raise RuntimeError(str(before.get("reason") or "鉴宝十次开关状态不完整"))
    if bool(before["ten_draw_enabled"]) == bool(ten_draw):
        return {"result": "already_set", "ten_draw_enabled": bool(ten_draw)}

    frame = runtime.cur_frame(update=True)
    runtime.click_shape(
        KUNLUN_MAIN_SCENE_ID,
        KUNLUN_TEN_DRAW_TOGGLE_SHAPE,
        frame_data_url=frame,
    )
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = read_bothdraw_ten_draw_runtime(
            expected_activity_id=activity_id,
            expected_x=int(lottery.get("x") or 0),
            expected_y=int(lottery.get("y") or 0),
        )
        if (
            last.get("complete")
            and isinstance(last.get("ten_draw_enabled"), bool)
            and bool(last["ten_draw_enabled"]) == bool(ten_draw)
        ):
            return {"result": "changed", "ten_draw_enabled": bool(ten_draw)}
        time.sleep(max(0.05, float(poll_seconds)))
    raise RuntimeError(
        "切换鉴宝次数后未从 V_UseTenTimes 复验成功："
        f"{str((last or {}).get('reason') or '状态未变化')}"
    )


def _reward_item_count(reward: str, item_id: int) -> int:
    if item_id <= 0:
        return 0
    match = re.fullmatch(rf"Item\|{item_id}_(\d+)", reward.strip())
    return int(match.group(1)) if match else 0


def _record_snapshot(snapshot: dict[str, Any], *, instance_id: str) -> None:
    from backend.db import engine

    with Session(engine) as session:
        record_kunlun_lottery_point(
            session,
            snapshot=snapshot,
            instance_id=instance_id,
        )


def _open_main(runtime: Any) -> Any:
    return open_kunlun_tab(runtime, "昆仑秘藏")


def _record_for_spec(snapshot: dict[str, Any], instance_id: str) -> None:
    _record_snapshot(snapshot, instance_id=instance_id)


def _spec() -> BothdrawLotterySpec:
    return BothdrawLotterySpec(
        activity_label="昆仑秘藏",
        main_scene_id=KUNLUN_MAIN_SCENE_ID,
        draw_shape="鉴宝",
        cumulative_reward_shape=KUNLUN_CUMULATIVE_REWARD_SHAPE,
        cumulative_reward_slots=KUNLUN_CUMULATIVE_REWARD_SLOTS,
        draw_result_scene_id=KUNLUN_DRAW_RESULT_SCENE_ID,
        draw_result_close_shape="继续",
        main_page_name="昆仑秘藏",
        open_main_page=_open_main,
        read_page=lambda runtime: read_kunlun_page(runtime, update=True),
        read_lottery=read_bothdraw_lottery_runtime,
        read_cumulative_rewards=read_bothdraw_cumulative_rewards_runtime,
        resolve_instance_id=kunlun_week_instance_id,
        record_snapshot=_record_for_spec,
    )


def draw_kunlun_once(
    runtime: Any,
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 0.5,
    requested_batch_size: int | None = None,
) -> dict[str, Any]:
    return draw_bothdraw_once(
        runtime,
        _spec(),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        requested_batch_size=requested_batch_size,
    )


def claim_kunlun_cumulative_rewards(
    runtime: Any,
    *,
    timeout_seconds: float = 15.0,
    poll_seconds: float = 0.5,
    max_clicks: int = 16,
) -> dict[str, Any]:
    return claim_bothdraw_cumulative_rewards(
        runtime,
        _spec(),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        max_clicks=max_clicks,
    )


def close_kunlun_draw_result(
    runtime: Any,
    *,
    timeout_seconds: float = 30.0,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    return close_bothdraw_result(
        runtime,
        _spec(),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def complete_kunlun_lottery(
    runtime: Any,
    *,
    max_rounds: int = 256,
    allow_single_draws: bool = True,
) -> dict[str, Any]:
    spec = _spec()
    spec.require_executable_assets()
    spec.open_main_page(runtime)
    rounds: list[dict[str, Any]] = []
    for round_index in range(max(1, int(max_rounds))):
        state = _read_coherent_state()
        decision = decide_kunlun_next_draw(
            state,
            allow_single_draws=allow_single_draws,
        )
        if decision.action == "claim_rewards":
            claim = claim_kunlun_cumulative_rewards(runtime)
            rounds.append({"round": round_index + 1, "action": "claim_rewards", "claim": claim})
            continue
        if decision.action.startswith("stop_"):
            return {
                "result": "success",
                "round_count": len(rounds),
                "rounds": rounds,
                "stop_reason": decision.action,
                "final_state": state,
            }

        expected = int(decision.expected_batch_size)
        mode = ensure_kunlun_draw_mode(runtime, ten_draw=expected == 10)
        draw = draw_kunlun_once(runtime, requested_batch_size=expected)
        if int(draw.get("dx") or 0) != expected:
            raise RuntimeError(
                f"昆仑鉴宝实际批次与已复验开关不一致：expected={expected}, "
                f"actual={int(draw.get('dx') or 0)}"
            )
        close = close_kunlun_draw_result(runtime)
        claim = claim_kunlun_cumulative_rewards(runtime)
        rounds.append(
            {
                "round": round_index + 1,
                "action": decision.action,
                "mode": mode,
                "draw": draw,
                "close_result": close,
                "claim": claim,
            }
        )
    raise RuntimeError(f"昆仑抽奖流程超过安全轮次上限：{max_rounds}")


__all__ = [
    "claim_kunlun_cumulative_rewards",
    "close_kunlun_draw_result",
    "complete_kunlun_lottery",
    "decide_kunlun_next_draw",
    "draw_kunlun_once",
    "ensure_kunlun_draw_mode",
]
