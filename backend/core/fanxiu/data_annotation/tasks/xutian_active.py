from __future__ import annotations

"""Occurrence-scoped coordinator for the gameplay-ranking Xutian checkpoint.

The gameplay-ranking lifecycle owns this coordinator.  It deliberately runs at
most one formally-owned native-auto batch per checkpoint attempt; it is not a
replacement Scheduler Job.  Runtime/DB occurrence identity is checked before
planning, while every item-consuming option remains fail-closed unless the
parent payload explicitly authorizes it.
"""

from datetime import datetime, time as time_cls, timedelta
import threading
from typing import Any, Iterator, Mapping, Sequence

from sqlmodel import Session, select

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.tasks.xutian_native_auto import (
    XUTIAN_NATIVE_AUTO_BATCHES_KEY,
    XUTIAN_NATIVE_AUTO_PROBE_CHALLENGES,
    XUTIAN_NATIVE_AUTO_START_MARK,
    execute_xutian_native_auto_job,
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


def execute_xutian_task_reward_gate(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
    activity: FanxiuExchangeActivity,
) -> Iterator[Any]:
    """Fail-closed seam for the first normal Xutian business phase.

    The real GUI/QuestMgr transaction is intentionally not guessed here.  The
    current asset tree has no verified Xutian task-page contract yet.  Keeping
    this as a generator-shaped business seam lets that transaction be added
    without allowing budget planning or a native batch to run ahead of it.

    A successful implementation must return ``status=completed``, prove a
    complete Runtime snapshot and leave no authorized reward unclaimed.  It
    may report rewards claimed during this attempt in ``claimed_task_ids``.
    """

    del runner, ctx, payload, stop_event, occurrence, activity
    if False:
        yield None
    return {
        "status": "blocked",
        "phase": "task_rewards",
        "snapshot_complete": False,
        "authorized_remaining": [],
        "claimed_task_ids": [],
        "message": "虚天任务奖励页尚无已验证 GUI/QuestMgr 工程实现",
    }


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

    ``required_new_currency`` is the fresh remaining gap supplied by the
    ranking planner. The first batch initializes a bounded 10-run yield probe.
    Once a current-occurrence yield exists, the native panel uses its actual
    maximum and the backend stops it against a fresh Runtime wallet target.
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

    rows = _batch_rows(activity)
    yield_rows = [row for row in rows if row.get("yield_eligible") is not False]
    bootstrap_rows = [
        row
        for row in rows
        if row.get("wallet_bootstrap") is True
        and row.get("yield_eligible") is False
    ]
    budget_bootstrap = False
    first_yield_probe = False
    if "required_new_currency" in payload:
        required = int(payload.get("required_new_currency") or 0)
    else:
        try:
            required = _fresh_required_new_currency(activity)
        except RuntimeError as exc:
            # Entering Xutian initializes both its wallet and activity-shop
            # models.  On a fresh occurrence, allow only the fixed 10-run
            # probe to bootstrap those facts; every later batch still requires
            # a fresh, fully budgeted exchange plan.
            freshness_blocked = "预算 freshness 门禁失效" in str(exc)
            if not freshness_blocked:
                raise
            if rows:
                # A one-run wallet bootstrap is deliberately not a yield
                # sample.  If it is the only current-occurrence evidence,
                # permit exactly one fixed 10-run probe to establish yield;
                # after that, stale shop planning fails closed again.
                if yield_rows or len(bootstrap_rows) != len(rows):
                    raise
                first_yield_probe = True
            required = 1
            budget_bootstrap = True
    if required < 0:
        raise ValueError("虚天 active required_new_currency 不能为负数")
    if required == 0:
        return {
            "status": "completed",
            "message": "虚天殿当前 occurrence 的纳元晶目标已达到",
            "required_new_currency": 0,
            "performed_actions": False,
            "phase": "completed",
            "task_rewards": task_rewards,
        }

    if yield_rows and _explicit_true(payload, "disable_xutian_scaled_batch"):
        return _pending(
            "虚天 Runtime 目标批次已被 payload 人工暂停，保留给玩法榜窗口复查",
            occurrence,
            phase="budget",
            next_phase="task_rewards",
            task_rewards=task_rewards,
            plan={
                "requested_challenges": "max",
                "planning_mode": "runtime_target",
                "required_new_currency": required,
            },
        )

    native_payload = dict(payload)
    # Once a measured yield exists, let the native panel run at its actual
    # maximum and stop against the live wallet. The 10-run first probe stays
    # bounded until the activity's budget and wallet are initialized.
    native_payload["requested_challenges"] = (
        "max" if yield_rows and not budget_bootstrap else XUTIAN_NATIVE_AUTO_PROBE_CHALLENGES
    )
    if yield_rows and not budget_bootstrap:
        native_payload["required_new_currency"] = required
    native_payload["allow_item_refill"] = _explicit_true(
        payload, "allow_xutian_item_refill"
    )
    native_payload["allow_boost_items"] = _explicit_true(
        payload, "allow_xutian_boost_items"
    )
    native_payload["allow_unloaded_wallet_bootstrap"] = (
        budget_bootstrap and not first_yield_probe
    )
    native_result = yield from execute_xutian_native_auto_job(
        runner, ctx, native_payload, stop_event
    )
    observation = dict(native_result.get("observation") or {})
    completed = int(observation.get("completed_challenges") or 0)
    delta = int(observation.get("currency_delta") or 0)
    if native_result.get("wallet_bootstrap") is True:
        if completed != 1 or observation.get("yield_eligible") is not False:
            raise RuntimeError("虚天钱包引导批次缺少精确一次 Runtime 完成证据")
        return _pending(
            "虚天 active 已完成 1 次钱包引导并建立绝对基线，等待 10 次收益探针",
            occurrence,
            phase="native_batch",
            next_phase="task_rewards",
            task_rewards=task_rewards,
            observation=observation,
            native_result=native_result,
        )
    if completed <= 0 or delta <= 0:
        raise RuntimeError("虚天 active 单批缺少 Runtime 完成次数或正向钱包增量")
    if completed < int(observation.get("requested_challenges") or 0) and delta < required:
        raise RuntimeError("虚天 active 提前停机但纳元晶目标尚未达到")
    if budget_bootstrap or first_yield_probe:
        return _pending(
            f"虚天 active 完成 {completed} 次模型引导探针，纳元晶 +{delta}，等待刷新兑换预算",
            occurrence,
            phase="native_batch",
            next_phase="task_rewards",
            task_rewards=task_rewards,
            observation=observation,
            native_result=native_result,
        )
    remaining = max(0, required - delta)
    if remaining == 0:
        return _pending(
            f"虚天 active 完成 {completed} 次，纳元晶 +{delta}，目标已达到；等待批次后任务奖励复查",
            occurrence,
            phase="native_batch",
            next_phase="task_rewards",
            task_rewards=task_rewards,
            required_new_currency=0,
            observation=observation,
            native_result=native_result,
        )
    return _pending(
        f"虚天 active 完成 {completed} 次探针，纳元晶 +{delta}，仍缺 {remaining}",
        occurrence,
        phase="native_batch",
        next_phase="task_rewards",
        task_rewards=task_rewards,
        required_new_currency=remaining,
        observation=observation,
        native_result=native_result,
    )


__all__ = [
    "execute_xutian_task_reward_gate",
    "execute_xutian_active_checkpoint",
    "validate_xutian_active_occurrence",
]
