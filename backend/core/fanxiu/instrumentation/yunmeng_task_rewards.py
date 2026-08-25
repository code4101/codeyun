from __future__ import annotations

"""Strict read-only selection of the live Yunmeng task-reward ladder."""

from collections.abc import Callable, Mapping
from typing import Any

from backend.core.fanxiu.instrumentation.daily_task_rewards import (
    YUNMENG_TASK_REWARD_SPECS,
    read_activity_task_reward_snapshots,
    read_activity_task_reward_fast_snapshot,
)


YUNMENG_TASK_REWARD_DOMAIN_ORDER = tuple(
    spec.key for spec in YUNMENG_TASK_REWARD_SPECS
)

YUNMENG_TASK_TAB_ORDER = ("cultivation", "score", "ranking")


def partition_yunmeng_authorized_tasks(snapshot: Mapping[str, Any]) -> dict[str, list[int]]:
    """Partition the exact server-authorized IDs by their visible task tab.

    The generated config fixes the three subtypes at 6/13/18, but the live
    score ladder has two mutually exclusive ID ranges.  Partitioning only the
    selected snapshot's authorization preserves that distinction and gives a
    future GUI adapter one independently verifiable list per tab.
    """

    authorized = snapshot.get("authorized_claim_task_ids")
    if (
        not snapshot.get("ok")
        or not snapshot.get("complete")
        or not isinstance(authorized, list)
    ):
        return {key: [] for key in YUNMENG_TASK_TAB_ORDER}
    selected_domain = snapshot.get("selected_domain") or snapshot.get("domain")
    spec = next(
        (item for item in YUNMENG_TASK_REWARD_SPECS if item.key == selected_domain),
        None,
    )
    if spec is None:
        return {key: [] for key in YUNMENG_TASK_TAB_ORDER}
    task_ids = tuple(spec.task_ids)
    groups = {
        "cultivation": set(task_ids[:8]),
        "score": set(task_ids[8:16]),
        "ranking": set(task_ids[16:]),
    }
    return {
        key: [int(task_id) for task_id in authorized if int(task_id) in groups[key]]
        for key in YUNMENG_TASK_TAB_ORDER
    }


def plan_yunmeng_task_reward_claim(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build the fail-closed decision consumed by the future GUI transaction."""

    authorized = snapshot.get("authorized_claim_task_ids")
    if not snapshot.get("ok") or not snapshot.get("complete"):
        return {
            "status": "fail_closed",
            "reason": str(snapshot.get("reason") or "云梦任务事实不完整"),
            "authorized_task_ids": [],
            "tabs": {key: [] for key in YUNMENG_TASK_TAB_ORDER},
        }
    if not isinstance(authorized, list):
        return {
            "status": "fail_closed",
            "reason": "云梦任务快照缺少严格授权列表",
            "authorized_task_ids": [],
            "tabs": {key: [] for key in YUNMENG_TASK_TAB_ORDER},
        }
    if not authorized:
        state = snapshot.get("state")
        return {
            "status": (
                "already_claimed" if state == "already_claimed" else "nothing_claimable"
            ),
            "reason": (
                "全部奖励已经领取" if state == "already_claimed" else "当前没有可领取任务"
            ),
            "authorized_task_ids": [],
            "tabs": {key: [] for key in YUNMENG_TASK_TAB_ORDER},
        }
    tabs = partition_yunmeng_authorized_tasks(snapshot)
    partitioned = [task_id for key in YUNMENG_TASK_TAB_ORDER for task_id in tabs[key]]
    if sorted(partitioned) != sorted(int(task_id) for task_id in authorized):
        return {
            "status": "fail_closed",
            "reason": "严格授权任务无法完整归入云梦三个任务页",
            "authorized_task_ids": [],
            "tabs": {key: [] for key in YUNMENG_TASK_TAB_ORDER},
        }
    return {
        "status": "ready",
        "reason": "可进入云梦任务页执行局部领取事务",
        "authorized_task_ids": [int(task_id) for task_id in authorized],
        "tabs": tabs,
    }


def verify_yunmeng_task_reward_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify one complete GUI transaction against two QuestMgr snapshots."""

    intended = [int(item) for item in before.get("authorized_claim_task_ids") or []]
    before_claimed = {int(item) for item in before.get("claimed_task_ids") or []}
    after_claimed = {int(item) for item in after.get("claimed_task_ids") or []}
    before_domain = before.get("selected_domain") or before.get("domain")
    after_domain = after.get("selected_domain") or after.get("domain")
    reason = ""
    if not before.get("ok") or not before.get("complete"):
        reason = "领取前云梦任务事实不完整"
    elif not intended:
        reason = "领取前没有严格授权任务"
    elif not after.get("ok") or not after.get("complete"):
        reason = "领取后云梦任务事实不完整"
    elif before_domain != after_domain:
        reason = "领取前后云梦任务梯度发生变化"
    elif after_claimed - before_claimed != set(intended):
        reason = "领取后 claimed 集合未形成精确授权迁移"
    elif list(after.get("authorized_claim_task_ids") or []):
        reason = "领取后仍残留可领取任务"
    return {
        "ok": not reason,
        "reason": reason or "QuestMgr 已确认全部授权任务精确迁移为已领取",
        "selected_domain": before_domain,
        "intended_task_ids": intended,
        "newly_claimed_task_ids": sorted(after_claimed - before_claimed),
    }


def select_live_yunmeng_task_reward_snapshot(
    snapshots: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Select exactly one complete config ladder and fail closed otherwise."""

    complete = [
        dict(snapshots[key])
        for key in YUNMENG_TASK_REWARD_DOMAIN_ORDER
        if key in snapshots
        and snapshots[key].get("ok")
        and snapshots[key].get("available")
        and snapshots[key].get("complete")
        and snapshots[key].get("state") != "ambiguous"
    ]
    if len(complete) != 1:
        return {
            "ok": False,
            "available": bool(complete),
            "complete": False,
            "state": "ambiguous",
            "authorized_claim_task_ids": [],
            "reason": (
                "未识别到完整云梦任务梯度"
                if not complete
                else f"同时识别到 {len(complete)} 套云梦任务梯度"
            ),
            "candidate_domains": [item.get("domain") for item in complete],
        }
    selected = complete[0]
    return {
        **selected,
        "selected_domain": selected.get("domain"),
        "variant_count": 1,
    }


def read_yunmeng_task_reward_snapshot(
    *,
    reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read all retained variants and return the single live task ladder."""

    if reader is None:
        batch = read_activity_task_reward_snapshots(YUNMENG_TASK_REWARD_DOMAIN_ORDER)
        snapshots = dict(batch.get("domains") or {})
    else:
        snapshots = {key: reader(key) for key in YUNMENG_TASK_REWARD_DOMAIN_ORDER}
    return select_live_yunmeng_task_reward_snapshot(snapshots)


def read_selected_yunmeng_task_reward_fast_snapshot(
    selected_domain: str,
    *,
    expected_claimed_task_id: int | None = None,
) -> dict[str, Any]:
    """Re-read one already selected live ladder after a single GUI click."""

    if selected_domain not in YUNMENG_TASK_REWARD_DOMAIN_ORDER:
        raise ValueError(f"未知云梦任务梯度: {selected_domain}")
    snapshot = read_activity_task_reward_fast_snapshot(
        selected_domain,
        expected_claimed_task_id=expected_claimed_task_id,
    )
    return {**snapshot, "selected_domain": selected_domain}
