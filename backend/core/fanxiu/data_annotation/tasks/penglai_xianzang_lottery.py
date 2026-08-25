from __future__ import annotations

"""Penglai Xianzang binding and its two-phase draw policy."""

import time
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.activity.penglai_xianzang_lottery import (
    list_xianzang_lottery_points,
    record_xianzang_lottery_point,
    xianzang_week_instance_id,
)
from backend.core.fanxiu.activity.lottery_observation_ledger import (
    build_paired_draw_observations,
)
from backend.core.fanxiu.activity.lottery_strategy import (
    LotteryDecision,
    LotteryGoal,
    LotteryPolicy,
    decide_lottery_action,
)
from backend.core.fanxiu.data_annotation.tasks.bothdraw_lottery import (
    BothdrawLotterySpec,
    _merge_draw_observation,
    claim_bothdraw_cumulative_rewards,
    close_bothdraw_result,
    draw_bothdraw_once,
)
from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_navigation import (
    XIANZANG_MAIN_SCENE_ID,
    enter_xianzang,
    leave_xianzang,
    open_xianzang_tab,
    read_xianzang_page,
)
from backend.core.fanxiu.instrumentation.bothdraw import (
    read_bothdraw_cumulative_rewards_runtime,
    read_bothdraw_lottery_runtime,
)
from backend.core.fanxiu.instrumentation.bothdraw_toggle import (
    read_bothdraw_ten_draw_runtime,
)


XIANZANG_CUMULATIVE_REWARD_SHAPE = "累抽奖励"
XIANZANG_CUMULATIVE_REWARD_SLOTS = 4
XIANZANG_TEN_DRAW_TOGGLE_SHAPE = "鉴宝十次"
XIANZANG_DRAW_RESULT_SCENE_ID = 451


def _record_snapshot(snapshot: dict[str, Any], *, instance_id: str) -> None:
    from backend.db import engine

    with Session(engine) as session:
        record_xianzang_lottery_point(
            session,
            snapshot=snapshot,
            instance_id=instance_id,
        )


def _open_main(runtime: Any) -> Any:
    return open_xianzang_tab(runtime, "蓬莱仙藏")


def _record_for_spec(snapshot: dict[str, Any], instance_id: str) -> None:
    _record_snapshot(snapshot, instance_id=instance_id)


def _spec() -> BothdrawLotterySpec:
    # Build on demand so monkeypatches used by focused tests and Runtime probes
    # continue to replace the module-level dependencies.
    return BothdrawLotterySpec(
        activity_label="蓬莱仙藏",
        main_scene_id=XIANZANG_MAIN_SCENE_ID,
        draw_shape="鉴宝",
        cumulative_reward_shape=XIANZANG_CUMULATIVE_REWARD_SHAPE,
        cumulative_reward_slots=XIANZANG_CUMULATIVE_REWARD_SLOTS,
        draw_result_scene_id=XIANZANG_DRAW_RESULT_SCENE_ID,
        draw_result_close_shape="继续",
        main_page_name="蓬莱仙藏",
        open_main_page=_open_main,
        read_page=lambda runtime: read_xianzang_page(runtime, update=True),
        read_lottery=read_bothdraw_lottery_runtime,
        read_cumulative_rewards=read_bothdraw_cumulative_rewards_runtime,
        resolve_instance_id=xianzang_week_instance_id,
        record_snapshot=_record_for_spec,
    )


def draw_xianzang_once(
    runtime: Any,
    *,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 0.5,
    requested_batch_size: int | None = None,
) -> dict[str, Any]:
    recovered = _reconcile_pending_xianzang_draw()
    if recovered is not None:
        return recovered
    return draw_bothdraw_once(
        runtime,
        _spec(),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        requested_batch_size=requested_batch_size,
    )


def _reconcile_pending_xianzang_draw() -> dict[str, Any] | None:
    """Close a persisted click whose post-click Runtime read was interrupted.

    ``before_draw`` is committed before the irreversible click.  A process-map
    race after that click must therefore be reconciled against the current
    authoritative cumulative counter before another click is allowed.  Only a
    positive, bounded delta closes the old action; an unchanged or incomplete
    counter remains outcome-unknown and fails closed.
    """

    from backend.db import engine

    instance_id = xianzang_week_instance_id()
    with Session(engine) as session:
        samples = list_xianzang_lottery_points(
            session, instance_id=instance_id
        ).samples
    after_ids = {
        str(point.action_id)
        for point in samples
        if point.action_id and point.action_phase == "after_draw"
    }
    pending = [
        point
        for point in samples
        if point.action_id
        and point.action_phase == "before_draw"
        and str(point.action_id) not in after_ids
    ]
    if not pending:
        return None
    if len(pending) != 1:
        raise RuntimeError(f"蓬莱仙藏存在多个未闭环抽奖动作：{len(pending)}")

    point = pending[0]
    current = read_bothdraw_lottery_runtime()
    resources = read_bothdraw_cumulative_rewards_runtime()
    if not current.get("complete") or not resources.get("complete"):
        reason = current.get("reason") or resources.get("reason") or "运行态数据不完整"
        raise RuntimeError(f"蓬莱仙藏上次抽奖结果未知，拒绝再次鉴宝：{reason}")
    if int(current.get("activity_id") or 0) != int(point.activity_id or 0):
        raise RuntimeError("蓬莱仙藏上次抽奖与当前活动实例不一致，拒绝再次鉴宝")

    dx = int(current.get("x") or 0) - int(point.x)
    requested = int(point.requested_batch_size or 0)
    if dx == 0:
        raise RuntimeError(
            "蓬莱仙藏上次鉴宝已点击但结果未闭环，当前累计抽数未增加；"
            "拒绝自动重放不可逆动作"
        )
    if dx < 0 or requested <= 0 or dx > requested:
        raise RuntimeError(
            f"蓬莱仙藏未闭环抽奖增量异常：dx={dx}, requested={requested}"
        )

    action_id = str(point.action_id)
    before = {
        "complete": True,
        "captured_at": point.captured_at,
        "activity_id": int(point.activity_id or 0),
        "x": int(point.x),
        "y": int(point.y),
        "selected_big_reward": {
            "library_id": int(point.selected_library_id),
            "item_id": int(point.selected_item_id),
            "name": str(point.selected_item_name),
        },
        "selected_library_id": int(point.selected_library_id),
        "action_id": action_id,
        "observation_kind": "before_draw",
        "action_phase": "before_draw",
        "ledger_protocol": str(point.ledger_protocol or "paired_draw_v1"),
        "draw_mode": str(point.draw_mode or "single_draw"),
        "requested_batch_size": requested,
        "available_draws": point.available_draws,
        "progress": point.progress,
    }
    after = _merge_draw_observation(
        current,
        resources,
        observation_kind="after_draw",
        action_id=action_id,
        batch_size=dx,
        draw_mode=str(point.draw_mode or "single_draw"),
        before_observation=before,
    )
    _before, after = build_paired_draw_observations(
        before,
        after,
        action_id=action_id,
        draw_mode=str(point.draw_mode or "single_draw"),
        requested_batch_size=requested,
    )
    _record_snapshot(after, instance_id=instance_id)
    return {
        "result": "success",
        "recovered_pending_action": True,
        "instance_id": instance_id,
        "before": {"x": int(point.x), "y": int(point.y)},
        "after": {"x": int(after["x"]), "y": int(after["y"])},
        "dx": dx,
        "dy": int(after["y"]) - int(point.y),
        "selected_big_reward": dict(after.get("selected_big_reward") or {}),
        "draw_mode": str(after.get("draw_mode") or point.draw_mode or ""),
    }


def ensure_xianzang_draw_mode(
    runtime: Any,
    *,
    ten_draw: bool,
    timeout_seconds: float = 8.0,
    poll_seconds: float = 0.25,
) -> dict[str, Any]:
    """Set and re-read the real Bothdraw batch mode before consuming keys."""

    lottery = read_bothdraw_lottery_runtime()
    if not lottery.get("complete"):
        raise RuntimeError(str(lottery.get("reason") or "蓬莱仙藏活动身份不完整"))
    activity_id = int(lottery.get("activity_id") or 0)
    if activity_id <= 0:
        raise RuntimeError("蓬莱仙藏活动身份无效")
    before = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
    panel_reloaded = False
    if (
        not before.get("complete")
        and "NotLoaded:" in str(before.get("reason") or "")
    ):
        # Returning from the task tab can leave a visually complete #447 while
        # its Bothdraw panel is absent from UIShowMgr's active component tree.
        # Reopen the activity once before any draw/toggle click.  This is a
        # reversible panel-lifecycle repair; the same Bothdraw activity id is
        # still rechecked below, and a second NotLoaded remains a hard failure.
        leave_xianzang(runtime)
        enter_xianzang(runtime)
        panel_reloaded = True
        before = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
    if not before.get("complete") or not isinstance(
        before.get("ten_draw_enabled"), bool
    ):
        raise RuntimeError(str(before.get("reason") or "鉴宝十次开关状态不完整"))
    if bool(before["ten_draw_enabled"]) == bool(ten_draw):
        return {
            "result": "already_set",
            "ten_draw_enabled": bool(ten_draw),
            "panel_reloaded": panel_reloaded,
        }

    frame = runtime.cur_frame(update=True)
    runtime.click_shape(
        XIANZANG_MAIN_SCENE_ID,
        XIANZANG_TEN_DRAW_TOGGLE_SHAPE,
        frame_data_url=frame,
    )
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = read_bothdraw_ten_draw_runtime(expected_activity_id=activity_id)
        if (
            last.get("complete")
            and isinstance(last.get("ten_draw_enabled"), bool)
            and bool(last["ten_draw_enabled"]) == bool(ten_draw)
        ):
            return {
                "result": "changed",
                "ten_draw_enabled": bool(ten_draw),
                "panel_reloaded": panel_reloaded,
            }
        time.sleep(max(0.05, float(poll_seconds)))
    raise RuntimeError(
        "切换鉴宝次数后未从 V_UseTenTimes 复验成功："
        f"{str((last or {}).get('reason') or '状态未变化')}"
    )


def claim_xianzang_cumulative_rewards(
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


def close_xianzang_draw_result(
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


def decide_xianzang_next_draw(
    snapshot: dict[str, Any], *, preserve_terminal_remainder: bool
) -> LotteryDecision:
    """Map Penglai's two stages onto the shared exhaust-all policy."""

    return decide_lottery_action(
        {
            **snapshot,
            "progress": int(snapshot.get("progress") or snapshot.get("x") or 0),
            "hit_count": int(snapshot.get("selected_big_count") or snapshot.get("y") or 0),
        },
        policy=LotteryPolicy(
            goal=LotteryGoal("exhaust_all"),
            remainder_mode="defer" if preserve_terminal_remainder else "single",
        ),
    )


def complete_xianzang_lottery(
    runtime: Any,
    *,
    max_rounds: int = 256,
) -> dict[str, Any]:
    """Late phase: use ten-draws first, then single-draw every remaining key."""

    spec = _spec()
    spec.require_executable_assets()
    spec.open_main_page(runtime)
    rounds: list[dict[str, Any]] = []
    initial_claim = claim_xianzang_cumulative_rewards(runtime)
    for round_index in range(max(1, int(max_rounds))):
        state = read_bothdraw_cumulative_rewards_runtime()
        if not state.get("complete"):
            raise RuntimeError(str(state.get("reason") or "蓬莱仙藏抽奖资源状态不完整"))
        decision = decide_xianzang_next_draw(
            state, preserve_terminal_remainder=False
        )
        if decision.action == "stop":
            final_claim = claim_xianzang_cumulative_rewards(runtime)
            final_state = read_bothdraw_cumulative_rewards_runtime()
            if not final_state.get("complete"):
                raise RuntimeError(
                    str(final_state.get("reason") or "蓬莱仙藏抽奖终态不完整")
                )
            if int(final_state.get("available_draws") or 0) > 0:
                continue
            if final_state.get("claimable"):
                raise RuntimeError("蓬莱仙藏抽奖终态仍存在可领取累抽奖励")
            return {
                "result": "success",
                "round_count": len(rounds),
                "rounds": rounds,
                "initial_claim": initial_claim,
                "final_claim": final_claim,
                "final_state": final_state,
                "stop_reason": "draws_exhausted_and_rewards_claimed",
            }

        if decision.action == "claim_rewards":
            claim_xianzang_cumulative_rewards(runtime)
            continue
        expected = int(decision.expected_batch_size)
        mode = ensure_xianzang_draw_mode(runtime, ten_draw=expected == 10)
        draw = draw_xianzang_once(runtime, requested_batch_size=expected)
        if int(draw.get("dx") or 0) != expected:
            raise RuntimeError(
                "蓬莱仙藏实际批次与已复验开关不一致："
                f"expected={expected}, actual={int(draw.get('dx') or 0)}"
            )
        close = close_xianzang_draw_result(runtime)
        claim = claim_xianzang_cumulative_rewards(runtime)
        rounds.append(
            {
                "round": round_index + 1,
                "mode": mode,
                "draw": draw,
                "close_result": close,
                "claim": claim,
            }
        )
    raise RuntimeError(f"蓬莱仙藏抽奖流程超过安全轮次上限：{max_rounds}")


def complete_xianzang_config_ten_draws(
    runtime: Any,
    *,
    max_rounds: int = 64,
) -> dict[str, Any]:
    """First phase: claim milestones and spend only complete ten-draw batches.

    The remaining 0..9 keys are deliberately preserved for the 21:10 job,
    which uses :func:`complete_xianzang_lottery` to finish with single draws.
    """

    spec = _spec()
    spec.require_executable_assets()
    spec.open_main_page(runtime)
    rounds: list[dict[str, Any]] = []
    initial_claim = claim_xianzang_cumulative_rewards(runtime)
    for round_index in range(max(1, int(max_rounds))):
        state = read_bothdraw_cumulative_rewards_runtime()
        if not state.get("complete"):
            raise RuntimeError(str(state.get("reason") or "蓬莱仙藏抽奖资源状态不完整"))
        decision = decide_xianzang_next_draw(
            state, preserve_terminal_remainder=True
        )
        if decision.action == "stop":
            return {
                "result": "success",
                "round_count": len(rounds),
                "rounds": rounds,
                "initial_claim": initial_claim,
                "final_state": state,
                "stop_reason": "fewer_than_ten_draws_preserved_for_late_phase",
            }

        if decision.action == "claim_rewards":
            claim_xianzang_cumulative_rewards(runtime)
            continue
        mode = ensure_xianzang_draw_mode(runtime, ten_draw=True)
        draw = draw_xianzang_once(runtime, requested_batch_size=10)
        if int(draw.get("dx") or 0) != 10:
            raise RuntimeError(
                "蓬莱仙藏配置阶段实际批次与已复验十连开关不一致："
                f"expected=10, actual={int(draw.get('dx') or 0)}"
            )
        close = close_xianzang_draw_result(runtime)
        claim = claim_xianzang_cumulative_rewards(runtime)
        rounds.append(
            {
                "round": round_index + 1,
                "mode": mode,
                "draw": draw,
                "close_result": close,
                "claim": claim,
            }
        )
    raise RuntimeError(f"蓬莱仙藏配置阶段十连超过安全轮次上限：{max_rounds}")


__all__ = [
    "claim_xianzang_cumulative_rewards",
    "close_xianzang_draw_result",
    "complete_xianzang_lottery",
    "complete_xianzang_config_ten_draws",
    "decide_xianzang_next_draw",
    "draw_xianzang_once",
    "ensure_xianzang_draw_mode",
]
