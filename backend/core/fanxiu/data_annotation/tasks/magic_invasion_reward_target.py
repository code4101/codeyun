from __future__ import annotations

"""Reward-tier progression consumes fresh shop targets and measured batch yields."""
from datetime import datetime
from sqlmodel import Session
from backend.db import engine
from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
from backend.core.fanxiu.activity.magic_invasion_challenge_planning import (
    magic_invasion_milestones, plan_magic_invasion_reward_batch,
)
from backend.core.fanxiu.instrumentation.backpack import read_backpack_item_counts
from backend.core.fanxiu.instrumentation.magic_invasion_auto_running import read_magic_invasion_counters
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from .magic_invasion import MagicInvasionOccurrence
from .magic_invasion_native_auto import run_magic_invasion_auto_batch
from .magic_invasion_native_auto_timing import load_magic_invasion_auto_timing_state
from .magic_invasion_reward_ledger import load_magic_invasion_reward_state


def run_magic_invasion_reward_target(context, occurrence, *, activity_id, max_batches=80):
    with Session(engine) as session:
        detail = list_exchange_activity_snapshot(session, activity_type="magic-invasion",
                                                 activity_id=activity_id).selected_activity
        if detail is None or not detail.budget_ready:
            raise RuntimeError("魔道奖励规划需要新鲜的兑换宝阁和钱包事实")
        milestones = magic_invasion_milestones([r.model_dump() for r in detail.shop_items])
        currency_type = detail.currency_type
    initialization = load_magic_invasion_auto_timing_state(occurrence)
    if not initialization.get("measurements"):
        raise RuntimeError("魔道首次100次采样尚未完成")
    for _ in range(max_batches):
        formal = load_magic_invasion_reward_state(occurrence)
        pending = formal.get("pending_batch")
        if pending:
            yield from run_magic_invasion_auto_batch(context, occurrence,
                count=int(pending["requested_exorcisms"]), phase="formal")
            continue
        wallet = read_wallet_currency_snapshot(currency_type, allow_discovery=True)
        counts = read_magic_invasion_counters()
        items, _ = read_backpack_item_counts([1010005], manager_key="magic-invasion-reward-capacity")
        rows = [*initialization["measurements"], *formal["measurements"]]
        duration = rows[-1]["duration_seconds"] / rows[-1]["completed_exorcisms"]
        # Fast mode expires one hour before the activity closes. Leave room
        # for configuration/confirmation instead of starting across that edge.
        available_seconds = max(0, occurrence.end_time_ms / 1000 - 3600 - datetime.now().timestamp() - 90)
        resource_capacity = counts["challenge_count"] + items.get(1010005, 0)
        time_capacity = int(available_seconds / duration)
        capacity = min(resource_capacity, time_capacity)
        plan = plan_magic_invasion_reward_batch(milestones=milestones,
            current_currency=wallet["exchange_currency"], cumulative_currency=wallet["cumulative_currency"],
            samples=rows, capacity=capacity)
        plan["resource_capacity"] = resource_capacity
        plan["time_capacity"] = time_capacity
        if not plan["count"]:
            yield from context.go_scene(34)
            return {"status": "completed" if plan["status"] == "completed" else "unavailable",
                    "outcome": plan["status"], "achieved": plan["status"] == "completed",
                    "message": ("魔道全部有限兑换档次预算已满足" if plan["status"] == "completed"
                                else "魔道上一批未产生正收益，本轮pass" if plan["reason"] == "no_positive_yield"
                                else f"魔道下一档需{plan['needed']}次，可运行{capacity}次，缺{plan['deficit']}次，本轮pass"),
                    "plan": plan, "magic_crystal": wallet["exchange_currency"]}
        yield from run_magic_invasion_auto_batch(context, occurrence, count=plan["count"], phase="formal")
    yield from context.go_scene(34)
    return {"status": "pending", "message": "魔道滚动批次达到本次执行上限"}


def native_magic_invasion_occurrence(occurrence):
    return MagicInvasionOccurrence(
        occurrence_id=occurrence.runtime_id, activity_id=occurrence.activity_id,
        runtime_id=int(occurrence.runtime_id), start_time_ms=int(occurrence.start_at.timestamp()*1000),
        end_time_ms=int(occurrence.end_at.timestamp()*1000), server_count=occurrence.cross_count,
        mode="cross" if occurrence.cross_count > 1 else "server")


def execute_magic_invasion_evening_initialization(runner, ctx, payload, stop_event, *, occurrence):
    """Base exploration → exactly one 100-run sample → task rewards.

    A persisted sample is reused across attempts. Recover an authorized batch
    before navigation, and keep reward collection outside its yield baseline.
    """
    from .magic_invasion_compound import (
        execute_magic_invasion_compound_checkpoint, claim_magic_invasion_occurrence_rewards,
    )
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    native_occurrence = native_magic_invasion_occurrence(occurrence)
    state = load_magic_invasion_auto_timing_state(native_occurrence)
    if state.get("pending_batch"):
        result = yield from run_magic_invasion_auto_batch(context, native_occurrence)
        state = result["state"]
    elif not state["measurements"]:
        yield from execute_magic_invasion_compound_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence, claim_rewards=False)
        counts = read_magic_invasion_counters()
        items, _ = read_backpack_item_counts([1010005], manager_key="magic-invasion-initial-capacity")
        capacity = counts["challenge_count"] + items.get(1010005, 0)
        if capacity < 100 or datetime.now().timestamp() >= native_occurrence.end_time_ms / 1000 - 3690:
            yield from context.go_scene(34)
            return {"status": "unavailable", "outcome": "pass", "achieved": False,
                    "message": "魔道首次100次所需资源或运行窗口不足",
                    "needed": 100, "capacity": capacity, "deficit": max(0, 100-capacity)}
        result = yield from run_magic_invasion_auto_batch(context, native_occurrence)
        state = result["state"]
    if not state["measurements"]:
        return {"status": "unavailable", "outcome": "pass", "message": "魔道首次采样未完成"}
    tasks = yield from claim_magic_invasion_occurrence_rewards(context, occurrence)
    return {"status": "completed", "message": "魔道首次采样与任务奖励完成", "tasks": tasks}


def execute_magic_invasion_reward_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    """Resume initialization if needed, then fund each finite shop milestone."""
    from .magic_invasion_initialization import execute_magic_invasion_initialization_checkpoint
    native_occurrence = native_magic_invasion_occurrence(occurrence)
    pending = load_magic_invasion_reward_state(native_occurrence).get("pending_batch")
    if pending:
        context = runner._behavior_tree_context(ctx, stop_event=stop_event)
        yield from run_magic_invasion_auto_batch(context, native_occurrence,
            count=int(pending["requested_exorcisms"]), phase="formal")
    elif read_magic_invasion_counters().get("is_in_auto"):
        raise RuntimeError("魔道正在由外部启动自动挑战，保留运行现场")
    initial = yield from execute_magic_invasion_evening_initialization(
        runner, ctx, payload, stop_event, occurrence=occurrence)
    if initial["status"] != "completed":
        return initial
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    now = datetime.now().astimezone()
    shop = yield from execute_magic_invasion_initialization_checkpoint(
        runner, ctx, stop_event, occurrence=occurrence, captured_at=now, required_fact_watermark=now)
    return (yield from run_magic_invasion_reward_target(context, native_magic_invasion_occurrence(occurrence),
                                                       activity_id=shop["activity_id"]))
