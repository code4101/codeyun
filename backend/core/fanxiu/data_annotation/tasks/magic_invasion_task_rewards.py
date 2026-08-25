from __future__ import annotations

"""Strict GUI transaction for the two Magic Invasion task tabs."""

from collections.abc import Callable, Generator, Mapping
from typing import Any

from backend.core.fanxiu.instrumentation.magic_invasion_task_rewards import (
    read_magic_invasion_task_reward_snapshot,
)


MAGIC_HOME_SCENE = 509
MAGIC_TASK_EXORCISM_SCENE = 510
MAGIC_TASK_CULTIVATION_SCENE = 511
MAGIC_TASK_SCENE_BY_SUBTYPE = {
    1: MAGIC_TASK_EXORCISM_SCENE,
    2: MAGIC_TASK_CULTIVATION_SCENE,
}
MAGIC_TASK_TAB_BY_SUBTYPE = {
    1: "除魔页签",
    2: "修为页签",
}


TaskReader = Callable[..., dict[str, Any]]


def _task_evidence(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "state": str(snapshot.get("state") or ""),
        "claimable_task_ids": [
            int(value) for value in snapshot.get("claimable_task_ids") or ()
        ],
        "authorized_claim_task_ids": [
            int(value) for value in snapshot.get("authorized_claim_task_ids") or ()
        ],
        "claimed_task_ids": [
            int(value) for value in snapshot.get("claimed_task_ids") or ()
        ],
        "pending_task_ids": [
            int(value) for value in snapshot.get("pending_task_ids") or ()
        ],
        "source": str(snapshot.get("source") or ""),
        "protocol": str(snapshot.get("protocol") or ""),
    }


def _scene_id(value: Any) -> int:
    raw = getattr(value, "id", value)
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("魔道任务页等待结果缺少场景编号") from exc


def _authorized(snapshot: Mapping[str, Any], *, subtype: int) -> list[int]:
    subtype_by_id = {
        int(task_id): int(value)
        for task_id, value in dict(snapshot.get("task_subtypes") or {}).items()
    }
    unknown = [
        task_id
        for task_id in snapshot.get("authorized_claim_task_ids") or []
        if int(task_id) not in subtype_by_id
        or subtype_by_id[int(task_id)] not in MAGIC_TASK_SCENE_BY_SUBTYPE
    ]
    if unknown:
        raise RuntimeError(f"魔道任务 {int(unknown[0])} 缺少受支持的页签归属")
    return [
        int(task_id)
        for task_id in snapshot.get("authorized_claim_task_ids") or []
        if subtype_by_id[int(task_id)] == int(subtype)
    ]


def claim_magic_invasion_task_rewards(
    runtime: Any,
    *,
    activity_id: int,
    reader: TaskReader = read_magic_invasion_task_reward_snapshot,
) -> Generator[Any, Any, dict[str, Any]]:
    """Claim removing first rows, proving every click with exact taskId state."""

    snapshot = reader(int(activity_id))
    if not snapshot.get("ok") or not snapshot.get("available") or not snapshot.get("complete"):
        raise RuntimeError(str(snapshot.get("reason") or "魔道任务 Runtime 事实不完整"))
    initial_authorized = [
        int(value) for value in snapshot.get("authorized_claim_task_ids") or []
    ]
    initial_evidence = _task_evidence(snapshot)
    if not initial_authorized:
        return {
            "status": "already_settled",
            "claimed_task_ids": [],
            "before": initial_evidence,
            "after": initial_evidence,
            "message": "魔道任务当前无可领取奖励",
        }

    entered = yield from runtime.wait_click_then_view(
        MAGIC_HOME_SCENE,
        "任务",
        tuple(MAGIC_TASK_SCENE_BY_SUBTYPE.values()),
        timeout=20.0,
        label="魔道入侵：进入任务页",
    )
    current_scene = _scene_id(entered)
    claimed: list[int] = []

    for subtype in (1, 2):
        remaining = _authorized(snapshot, subtype=subtype)
        if not remaining:
            continue
        target_scene = MAGIC_TASK_SCENE_BY_SUBTYPE[subtype]
        if current_scene != target_scene:
            switched = yield from runtime.wait_click_then_view(
                current_scene,
                MAGIC_TASK_TAB_BY_SUBTYPE[subtype],
                target_scene,
                timeout=15.0,
                label=f"魔道入侵：切换任务页签 subtype={subtype}",
            )
            current_scene = _scene_id(switched)
        while remaining:
            expected = remaining[0]
            before_authorized = [
                int(value)
                for value in snapshot.get("authorized_claim_task_ids") or []
            ]
            yield from runtime.wait_click(
                current_scene,
                "首条任务领取区",
                timeout=10.0,
            )
            yield from runtime.wait_action_settle(1.0)
            after = reader(
                int(activity_id),
                expected_claimed_task_id=expected,
            )
            after_claimed = {
                int(value) for value in after.get("claimed_task_ids") or []
            }
            after_authorized = [
                int(value)
                for value in after.get("authorized_claim_task_ids") or []
            ]
            if (
                not after.get("ok")
                or not after.get("available")
                or not after.get("complete")
                or after.get("expected_task_claimed") is not True
                or expected not in after_claimed
                or after_authorized
                != [task_id for task_id in before_authorized if task_id != expected]
            ):
                raise RuntimeError(
                    f"魔道任务 taskId={expected} 点击后未形成精确单步状态迁移"
                )
            claimed.append(expected)
            snapshot = after
            remaining = _authorized(snapshot, subtype=subtype)

    returned = yield from runtime.wait_click_then_view(
        current_scene,
        "活动主页",
        MAGIC_HOME_SCENE,
        timeout=20.0,
        label="魔道入侵：任务领取后返回活动主页",
    )
    if _scene_id(returned) != MAGIC_HOME_SCENE:
        raise RuntimeError("魔道任务领取后未返回活动主页")
    return {
        "status": "claimed",
        "claimed_task_ids": claimed,
        "before": initial_evidence,
        "after": _task_evidence(snapshot),
        "message": f"魔道任务领取 {len(claimed)} 档",
    }


__all__ = ["claim_magic_invasion_task_rewards"]
