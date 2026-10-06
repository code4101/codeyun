from __future__ import annotations

"""Occurrence-scoped coordinator for the gameplay-ranking Xutian checkpoint.

The gameplay-ranking lifecycle owns this coordinator.  It deliberately runs at
most one formally-owned native-auto batch per checkpoint attempt; it is not a
replacement Scheduler Job.  Runtime/DB occurrence identity is checked before
planning. Ranking boosts default ON under the owner's standing policy;
refill and other resource acquisition still require their own authorization.
"""

from datetime import datetime, time as time_cls, timedelta
import threading
from typing import Any, Iterator, Mapping, Sequence

from sqlmodel import Session, select

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    XUTIAN_NATIVE_AUTO_BATCHES_KEY,
    XUTIAN_NATIVE_AUTO_START_MARK,
    execute_xutian_native_auto_job,
    xutian_consumable_permissions,
)
from backend.models import FanxiuExchangeActivity


XUTIAN_ACTIVE_RETRY_DELAY = timedelta(minutes=10)
XUTIAN_ACTIVE_RECHECK_WINDOWS = (
    time_cls(20, 45),
    time_cls(21, 15),
    time_cls(21, 35),
)


def _epoch_milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.astimezone()
    return int(value.timestamp() * 1000)


def _stored_timestamp_milliseconds(value: Any) -> int:
    """Normalize legacy epoch-ms and current ISO activity evidence."""

    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    if text.lstrip("-").isdigit():
        return int(text)
    try:
        return _epoch_milliseconds(datetime.fromisoformat(text))
    except ValueError:
        return 0


def _activity_occurrence_identity(activity: FanxiuExchangeActivity) -> dict[str, Any]:
    evidence = dict(activity.evidence or {})
    return {
        "activity_type": str(activity.activity_type or ""),
        "runtime_id": str(evidence.get("period_record_id") or ""),
        "activity_id": int(evidence.get("game_activity_id") or 0),
        "cross_count": int(activity.cross_count or 0),
        "start_at_ms": _stored_timestamp_milliseconds(
            evidence.get("period_start_time")
        ),
        "end_at_ms": _stored_timestamp_milliseconds(
            evidence.get("period_end_time")
        ),
        # Runtime schedule normalizes a missing closePanelTime to endTime.
        "close_at_ms": _stored_timestamp_milliseconds(
            evidence.get("period_close_panel_time") or evidence.get("period_end_time")
        ),
        "start_date": str(activity.start_date or ""),
        "end_date": str(activity.end_date or ""),
    }


def validate_xutian_active_occurrence(
    occurrence: Any,
    activities: Sequence[FanxiuExchangeActivity],
) -> FanxiuExchangeActivity:
    """Return the unique aggregate that exactly belongs to ``occurrence``."""

    if str(getattr(occurrence, "activity_type", "")) != "xutian-palace":
        raise RuntimeError("虚天 active checkpoint 收到非虚天殿 occurrence")
    expected = {
        "activity_type": "xutian-palace",
        "runtime_id": str(getattr(occurrence, "runtime_id", "") or ""),
        "activity_id": int(getattr(occurrence, "activity_id", 0) or 0),
        "cross_count": int(getattr(occurrence, "cross_count", 0) or 0),
        "start_at_ms": _epoch_milliseconds(occurrence.start_at),
        "end_at_ms": _epoch_milliseconds(occurrence.end_at),
        "close_at_ms": _epoch_milliseconds(occurrence.close_at),
        "start_date": occurrence.start_at.date().isoformat(),
        "end_date": occurrence.end_at.date().isoformat(),
    }
    if any(expected[key] in {"", 0} for key in (
        "activity_id", "cross_count", "start_at_ms", "end_at_ms"
    )):
        raise RuntimeError(f"虚天 active occurrence 身份不完整：{expected!r}")
    # The schedule row id is an observation cursor, not occurrence identity.
    # It may drift within one activity period (for example 4080001400020 ->
    # 4080001400004).  Retain it in diagnostics but bind irreversible work only
    # to the stable business identity below.
    stable_keys = (
        "activity_type",
        "activity_id",
        "cross_count",
        "start_at_ms",
        "end_at_ms",
        "close_at_ms",
        "start_date",
        "end_date",
    )
    exact_instance_matches = [
        activity
        for activity in activities
        if str(activity.instance_key or "")
        == str(getattr(occurrence, "instance_key", "") or "")
    ]
    matches = exact_instance_matches or [
        activity
        for activity in activities
        if all(
            _activity_occurrence_identity(activity).get(key) == expected.get(key)
            for key in stable_keys
        )
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "虚天 active occurrence 无法唯一对齐活动聚合，拒绝不可逆批次："
            f"expected={expected!r}, matches={len(matches)}"
        )
    return matches[0]


def _load_xutian_activity(occurrence: Any) -> FanxiuExchangeActivity:
    from backend.db import engine

    with Session(engine) as session:
        activities = list(session.exec(select(FanxiuExchangeActivity).where(
            FanxiuExchangeActivity.activity_type == "xutian-palace"
        )).all())
        activity = validate_xutian_active_occurrence(occurrence, activities)
        # The detached object is read-only in this coordinator.  The native
        # executor owns its marker/observation transaction in a fresh session.
        session.expunge(activity)
        return activity


def _fresh_required_new_currency(activity: FanxiuExchangeActivity) -> int:
    """Calculate the current closing-goods gap from fresh plan and wallet facts."""

    from backend.core.fanxiu.activity.exchange_event import (
        list_exchange_activity_snapshot,
    )
    from backend.core.fanxiu.activity.exchange_planning import (
        calculate_exchange_currency_gap,
    )
    from backend.core.fanxiu.instrumentation.wallet import (
        read_wallet_currency_snapshot,
    )
    from backend.db import engine

    activity_id = str(activity.id or "")
    if not activity_id:
        raise RuntimeError("虚天 active 活动聚合缺少主键")
    with Session(engine) as session:
        detail = list_exchange_activity_snapshot(
            session,
            activity_type="xutian-palace",
            activity_id=activity_id,
        ).selected_activity
    if detail is None or str(getattr(detail, "id", "")) != activity_id:
        raise RuntimeError("虚天 active 兑换计划活动实例发生切换")
    if str(getattr(detail, "activity_type", "")) != "xutian-palace":
        raise RuntimeError("虚天 active 兑换计划活动类型错误")
    if not bool(getattr(detail, "is_active", False)):
        raise RuntimeError("虚天 active 当前 occurrence 已不在有效期")
    plan = dict(getattr(detail, "exchange_plan", None) or {})
    if not bool(getattr(detail, "budget_ready", False)) or not bool(
        plan.get("budget_ready")
    ):
        reason = str(
            getattr(detail, "budget_block_reason", "")
            or plan.get("budget_block_reason")
            or "原因未知"
        )
        raise RuntimeError(f"虚天 active 收尾道具预算 freshness 门禁失效：{reason}")
    closing = dict(dict(plan.get("target_budgets") or {}).get("收尾道具") or {})
    target_total = int(closing.get("target_total_tokens") or 0)
    target_remaining = int(closing.get("target_remaining_tokens") or 0)
    if target_total <= 0 or target_remaining < 0:
        raise RuntimeError("虚天 active 收尾道具预算缺少有效 target_total/remaining")
    currency_type = int(getattr(detail, "currency_type", 0) or 0)
    if currency_type <= 0 or currency_type != int(activity.currency_type or 0):
        raise RuntimeError("虚天 active 兑换计划币种与活动聚合不一致")
    wallet = read_wallet_currency_snapshot(currency_type, allow_discovery=True)
    evidence = dict(wallet.get("evidence") or {})
    if (
        wallet.get("source") != "runtime_memory"
        or int(wallet.get("currency_type") or 0) != currency_type
        or int(evidence.get("pid") or 0) <= 0
        or int(evidence.get("process_start_ticks") or 0) <= 0
    ):
        raise RuntimeError("虚天 active 钱包 Runtime 身份不完整")
    gap = calculate_exchange_currency_gap(
        target_total_tokens=target_total,
        target_remaining_tokens=target_remaining,
        current_currency=int(wallet["exchange_currency"]),
        cumulative_currency=int(wallet["cumulative_currency"]),
    )
    return int(gap.required_new_currency)


def _batch_rows(activity: FanxiuExchangeActivity) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in (dict(activity.evidence or {}).get(XUTIAN_NATIVE_AUTO_BATCHES_KEY) or ())
        if isinstance(item, Mapping)
    ]


def _retry_at(occurrence: Any, *, now: datetime | None = None) -> datetime:
    current = now or job_now()
    if current.tzinfo is None:
        current = current.astimezone()
    close_at = occurrence.close_at
    if close_at.tzinfo is None:
        close_at = close_at.astimezone()
    for window in XUTIAN_ACTIVE_RECHECK_WINDOWS:
        candidate = datetime.combine(current.date(), window, tzinfo=current.tzinfo)
        if current < candidate <= close_at:
            return candidate
    candidate = current + XUTIAN_ACTIVE_RETRY_DELAY
    return min(candidate, close_at)


def _pending(message: str, occurrence: Any, **extra: Any) -> dict[str, Any]:
    retry = _retry_at(occurrence)
    return {
        "status": "pending",
        "message": message,
        "retry_at": retry.isoformat(timespec="seconds"),
        **extra,
    }


def _explicit_true(payload: Mapping[str, Any], key: str) -> bool:
    return payload.get(key) is True


def plan_xutian_finite_batch(*, occurrence: Any, allow_item_refill: bool = False,
                            funding_deadline: datetime | None = None) -> dict[str, Any]:
    """Read current facts and plan one complete finite-tier batch, without clicks.

    Uses the shared 100-run initialization and measured next-tier policy. Owned
    refill items count toward capacity only with explicit caller authorization.
    Native execution separately verifies all auto settings before consumption.
    """
    from backend.core.fanxiu.activity.exchange_event import list_exchange_activity_snapshot
    from backend.core.fanxiu.activity.exchange_challenge_planning import exchange_challenge_milestones, plan_exchange_challenge_batch
    from backend.core.fanxiu.activity.exchange_planning import calculate_exchange_currency_gap
    from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
    from backend.core.fanxiu.instrumentation.xutian_runtime import read_xutian_resource_snapshot
    from backend.db import engine

    activity = _load_xutian_activity(occurrence)
    with Session(engine) as session:
        detail = list_exchange_activity_snapshot(session, activity_type="xutian-palace", activity_id=str(activity.id)).selected_activity
    if detail is None or detail.instance_key != occurrence.instance_key or not detail.budget_ready:
        raise RuntimeError("虚天有限档规划缺少本期新鲜宝阁预算")
    items = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in detail.shop_items]
    milestones = exchange_challenge_milestones(items)
    wallet = read_wallet_currency_snapshot(12, allow_discovery=True)
    resources = read_xutian_resource_snapshot(include_auto_settings=False)
    identity_keys = ("pid", "process_start_ticks")
    wallet_identity = tuple(int(dict(wallet.get("evidence") or {}).get(k) or 0) for k in identity_keys)
    resource_identity = tuple(int(dict(resources.get("evidence") or {}).get(k) or 0) for k in identity_keys)
    if wallet.get("source") != "runtime_memory" or min(wallet_identity) <= 0 or wallet_identity != resource_identity:
        raise RuntimeError("虚天有限档钱包与次数库存来自不同 Runtime 进程")
    capacity_key = "with_items" if allow_item_refill is True else "without_items"
    capacity = min(resources["capacity"][f"explore_{capacity_key}"], resources["capacity"][f"challenge_{capacity_key}"])
    samples = []
    for row in _batch_rows(activity):
        completed = int(row.get("completed_challenges") or 0)
        requested = int(row.get("requested_challenges") or 0)
        if row.get("yield_eligible") is not False and completed > 0 and requested <= completed <= requested + 2 and int(row.get("currency_delta") or 0) > 0:
            samples.append({"completed_attempts": completed, "requested_attempts": requested, "maximum_attempts": requested + 2, "currency_delta": int(row["currency_delta"])})
    highest = milestones[-1]
    gap = calculate_exchange_currency_gap(target_total_tokens=highest["target_total_tokens"], target_remaining_tokens=highest["target_remaining_tokens"], current_currency=int(wallet["exchange_currency"]), cumulative_currency=int(wallet["cumulative_currency"])).required_new_currency
    plan = {"status": "completed", "reason": "all_milestones_funded", "count": 0} if gap == 0 else plan_exchange_challenge_batch(milestones=milestones, current_currency=int(wallet["exchange_currency"]), cumulative_currency=int(wallet["cumulative_currency"]), samples=samples, capacity=capacity, now=job_now().astimezone(), activity_end_at=occurrence.end_at, minimum_batch=100, funding_deadline=funding_deadline)
    return {"instance_key": occurrence.instance_key, "highest_finite": highest, "highest_gap": gap, "plan": plan, "wallet": wallet, "resources": resources, "sample_count": len(samples), "allow_item_refill": allow_item_refill is True, "funding_deadline": funding_deadline.isoformat() if funding_deadline else None}


def execute_xutian_task_reward_gate(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    activity: FanxiuExchangeActivity,
) -> Iterator[Any]:
    """Claim current rewards before any new batch; verify exact QuestMgr IDs.

    Assets #928/#929 were captured from the real exploration/cultivation pages.
    Re-entry returns to the stable home instead of retaining a prior row intent.
    """
    from functools import partial
    from backend.core.fanxiu.data_annotation.tasks.xutian_open_collection import enter_xutian_activity_home
    from backend.core.fanxiu.data_annotation.tasks.xutian_task_rewards import XutianTaskRewardAssets, XutianTaskRewardPageAssets, claim_xutian_task_rewards
    from backend.core.fanxiu.instrumentation.xutian_task_rewards import read_xutian_task_reward_snapshot
    if str(activity.instance_key or "") != occurrence.instance_key:
        raise RuntimeError("虚天领奖门禁活动实例不一致")
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(34)
    home = yield from enter_xutian_activity_home(context, occurrence)
    if home != 616:
        raise RuntimeError(f"虚天任务页入口 #{home} 尚未真实验收")
    # QuestMgr may already hold the complete current ladder. Read it anew on
    # every attempt: no saved 'already claimed' flag may authorize this path.
    # An incomplete/unloaded ladder still takes the real page-loading path.
    from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError
    try:
        loaded = read_xutian_task_reward_snapshot(int(occurrence.activity_id))
    except FanxiuRuntimeMemoryError:
        loaded = {}
    if (loaded.get("ok") is True and loaded.get("available") is True
            and loaded.get("complete") is True
            and loaded.get("source") == "runtime_memory"
            and not loaded.get("authorized_claim_task_ids")):
        return {"status": "completed", "phase": "task_rewards",
                "snapshot_complete": True, "authorized_remaining": [],
                "claimed_task_ids": [], "after_snapshot": loaded,
                "gui_task_page_opened": False,
                "message": "虚天当前完整任务快照无可领取奖励"}
    assets = XutianTaskRewardAssets(home_scene_id=616, pages=(
        XutianTaskRewardPageAssets(key="探索", subtype=2, scene_id=928, tab_shape="探索页签"),
        XutianTaskRewardPageAssets(key="修炼", subtype=4, scene_id=929, tab_shape="修炼页签"),
    ), task_entry_shape="任务", first_row_claim_shape="首条任务领取区", home_shape="虚天殿")
    transaction = yield from claim_xutian_task_rewards(context, assets=assets, reader=partial(read_xutian_task_reward_snapshot, int(occurrence.activity_id)))
    after = transaction["after_snapshot"]
    yield from context.wait_scene([616], wait=20)
    return {"status": "completed", "phase": "task_rewards", "snapshot_complete": after.get("complete") is True,
            "authorized_remaining": list(after.get("authorized_claim_task_ids") or []),
            "claimed_task_ids": transaction["claimed_task_ids"], "after_snapshot": after,
            "gui_task_page_opened": True,
            "message": f"虚天任务奖励已核验，当前领取 {len(transaction['claimed_task_ids'])} 档"}


def execute_xutian_task_reward_job(runner: Any, ctx: dict[str, Any], payload: dict[str, Any], stop_event: threading.Event) -> Iterator[Any]:
    """Formal independently callable reward phase; never starts challenges."""
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    now = job_now().astimezone()
    occurrences = [o for o in discover_ranking_occurrences(read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True))
                   if o.activity_type == "xutian-palace" and o.start_at <= now < o.end_at
                   and (not payload.get("instance_key") or o.instance_key == payload["instance_key"])]
    if len(occurrences) != 1:
        raise RuntimeError("虚天当前领奖实例不唯一或已结束")
    occurrence = occurrences[0]
    result = yield from execute_xutian_task_reward_gate(runner, ctx, payload, stop_event, occurrence=occurrence, activity=_load_xutian_activity(occurrence))
    passed, result = _normalize_task_reward_gate(result, occurrence=occurrence)
    if not passed:
        raise RuntimeError(result["message"])
    control.record_world_discovery("xutian-task-rewards:" + occurrence.instance_key, {**result, "instance_key": occurrence.instance_key, "captured_at": now.isoformat()})
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(34)
    return {**result, "final_scene": 34}


def execute_xutian_finite_goal_job(runner: Any, ctx: dict[str, Any], payload: dict[str, Any], stop_event: threading.Event) -> Iterator[Any]:
    """Formal one-batch finite-tier intent, sharing the ranking active checkpoint.

    Pending is a business result, never a claim that the highest tier completed.
    Boosts follow the standing ON default; refill/supply need explicit flags.
    Each new call recomputes
    the whole phase sequence from current facts and reconciles a pending native
    batch before doing anything new.
    """
    from backend.core.fanxiu.activity.ranking_lifecycle import discover_ranking_occurrences
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control as control
    now = job_now().astimezone()
    occurrences = [o for o in discover_ranking_occurrences(read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True))
                   if o.activity_type == "xutian-palace" and o.start_at <= now < o.end_at
                   and (not payload.get("instance_key") or o.instance_key == payload["instance_key"])]
    if len(occurrences) != 1:
        raise RuntimeError("虚天有限档实例不唯一或已结束")
    occurrence = occurrences[0]
    result = yield from execute_xutian_active_checkpoint(runner, ctx, payload, stop_event, occurrence=occurrence)
    control.record_world_discovery("xutian-finite-goal:" + occurrence.instance_key, {**result, "instance_key": occurrence.instance_key, "captured_at": now.isoformat()})
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(34)
    return {**result, "final_scene": 34}


def _normalize_task_reward_gate(
    result: Any,
    *,
    occurrence: Any,
) -> tuple[bool, dict[str, Any]]:
    """Validate the injected task gate and normalize non-terminal outcomes."""

    if not isinstance(result, Mapping):
        raise RuntimeError("虚天任务奖励门禁没有返回结构化结果")
    normalized = dict(result)
    normalized.setdefault("phase", "task_rewards")
    status = str(normalized.get("status") or "").strip().lower()
    if status == "completed":
        if normalized.get("snapshot_complete") is not True:
            raise RuntimeError("虚天任务奖励门禁声称完成但 Runtime 快照不完整")
        remaining = list(normalized.get("authorized_remaining") or ())
        if remaining:
            raise RuntimeError(
                f"虚天任务奖励门禁完成后仍有可领取 taskId：{remaining[0]}"
            )
        normalized["authorized_remaining"] = []
        normalized["claimed_task_ids"] = [
            int(value) for value in normalized.get("claimed_task_ids") or ()
        ]
        return True, normalized
    if status not in {"pending", "blocked"}:
        raise RuntimeError(f"虚天任务奖励门禁返回未知状态：{status or '<empty>'}")
    normalized["status"] = status
    normalized.setdefault("message", "虚天任务奖励门禁尚未通过")
    normalized.setdefault("retry_at", _retry_at(occurrence).isoformat(timespec="seconds"))
    return False, normalized


def execute_xutian_active_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    """Run no more than one occurrence-bound Xutian native-auto batch.

    The shared finite-tier planner chooses 100 initial runs, then one complete
    next-tier batch from the last full measured yield. Every whole attempt
    checks task rewards first and re-reads wallet, shop and resource facts.
    """

    activity = _load_xutian_activity(occurrence)
    activity_evidence = dict(activity.evidence or {})
    if isinstance(activity_evidence.get(XUTIAN_NATIVE_AUTO_START_MARK), Mapping):
        # An already-started irreversible batch is not a new business phase.
        # Reconcile it before any reward-page click, budget read or fresh
        # action; the next whole attempt restarts from the reward gate.
        native_result = yield from execute_xutian_native_auto_job(
            runner, ctx, dict(payload), stop_event
        )
        return _pending(
            "虚天 active 已安全结算遗留自动挑战批次，下一 attempt 先检查任务奖励",
            occurrence,
            phase="native_recovery",
            next_phase="task_rewards",
            native_result=native_result,
        )

    gate_raw = yield from execute_xutian_task_reward_gate(
        runner,
        ctx,
        dict(payload),
        stop_event,
        occurrence=occurrence,
        activity=activity,
    )
    gate_passed, task_rewards = _normalize_task_reward_gate(
        gate_raw,
        occurrence=occurrence,
    )
    if not gate_passed:
        return task_rewards

    from backend.core.fanxiu.activity.exchange_challenge_planning import parse_exchange_funding_deadline
    funding_deadline = parse_exchange_funding_deadline(payload.get('funding_deadline'))
    facts = plan_xutian_finite_batch(
        occurrence=occurrence,
        allow_item_refill=_explicit_true(payload, "allow_xutian_item_refill"),
        funding_deadline=funding_deadline,
    )
    plan = facts["plan"]
    supply = {"status": "not_requested", "receipts": []}
    needed = int(plan.get("needed") or 0)
    if (_explicit_true(payload, "allow_xutian_sacred_supply") and 0 < needed <= 5000
            and plan["status"] in {"ready", "pass"}
            and plan.get("reason") in {"next_batch", "resource_insufficient"}):
        from .xutian_supply import ensure_xutian_batch_supply
        supply = yield from ensure_xutian_batch_supply(
            runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event),
            required_challenges=needed, resources=facts["resources"],
            allow_item_refill=_explicit_true(payload, "allow_xutian_item_refill"),
        )
        if supply["receipts"]:
            # Resource acquisition is a separate transaction; select the same
            # next outstanding tier again from fresh wallet/stock facts.
            facts = plan_xutian_finite_batch(
                occurrence=occurrence,
                allow_item_refill=_explicit_true(payload, "allow_xutian_item_refill"),
                funding_deadline=funding_deadline,
            )
            plan = facts["plan"]
    if plan["status"] == "completed":
        return {"status": "completed", "phase": "completed", "message": "本期虚天最高有限兑币档已满足，兑换与榜单仍独立核验", "task_rewards": task_rewards, "finite_facts": facts, "performed_actions": False}
    if plan["status"] != "ready":
        return _pending("虚天完整补档批次尚未满足执行条件：" + plan["reason"], occurrence, phase="budget", next_phase="task_rewards", task_rewards=task_rewards, finite_facts=facts)
    requested = int(plan["count"])
    if requested > 5000:
        return _pending("虚天下一有限档完整批次超过已验证原生单批上限，需核实规则", occurrence, phase="budget", task_rewards=task_rewards, finite_facts=facts)
    if facts["sample_count"] and _explicit_true(payload, "disable_xutian_scaled_batch"):
        return _pending("虚天后续测算批次被显式暂停", occurrence, phase="budget", task_rewards=task_rewards, finite_facts=facts)
    native_payload = dict(payload)
    # One full next-tier batch, never native max or a shortened capacity probe.
    native_payload.pop("target_currency", None)
    native_payload.pop("required_new_currency", None)
    native_payload.update(requested_challenges=requested,
                          allow_item_refill=_explicit_true(payload, "allow_xutian_item_refill"),
                          allow_boost_items=xutian_consumable_permissions(
                              {"allow_boost_items": payload.get("allow_xutian_boost_items", True)}
                          )["allow_boost_items"],
                          allow_unloaded_wallet_bootstrap=False)
    native_result = yield from execute_xutian_native_auto_job(runner, ctx, native_payload, stop_event)
    observation = dict(native_result.get("observation") or {})
    completed = int(observation.get("completed_challenges") or 0)
    delta = int(observation.get("currency_delta") or 0)
    if not requested <= completed <= requested + 2 or delta <= 0:
        raise RuntimeError("虚天有限档单批未形成完整请求次数与正向收益，不采用为规划样本")
    return _pending(f"虚天完整批次 {completed} 次，纳元晶 +{delta}；下一 attempt 重新领奖、读取并规划", occurrence,
                    phase="native_batch", next_phase="task_rewards", task_rewards=task_rewards,
                    observation=observation, finite_facts=facts, native_result=native_result,
                    sacred_supply=supply)


__all__ = [
    "execute_xutian_finite_goal_job",
    "execute_xutian_task_reward_job",
    "plan_xutian_finite_batch",
    "execute_xutian_task_reward_gate",
    "execute_xutian_active_checkpoint",
    "validate_xutian_active_occurrence",
]
