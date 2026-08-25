from __future__ import annotations

"""Optional local transaction for Yunmeng task rewards.

The concrete GUI adapter is intentionally separate: it may only be supplied
after a real Yunmeng task-page frame and its three tab assets have been
verified.  The transaction itself is already usable for the common no-op path
and fails closed when rewards exist but that page evidence is unavailable.
"""

from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from backend.core.fanxiu.instrumentation.yunmeng_task_rewards import (
    plan_yunmeng_task_reward_claim,
    read_selected_yunmeng_task_reward_fast_snapshot,
    read_yunmeng_task_reward_snapshot,
    verify_yunmeng_task_reward_transition,
)


class YunmengTaskRewardGuiAdapter(Protocol):
    def __call__(
        self,
        snapshot: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> Generator[Any, None, dict[str, Any]]: ...


YunmengTaskRewardReader = Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class YunmengTaskRewardGuiAssets:
    """Formal scenes required by the concrete three-tab GUI adapter."""

    home_scene_id: int
    task_scene_ids: Mapping[str, int]
    tab_shape_names: Mapping[str, str]
    task_entry_shape: str = "任务"
    first_row_claim_shape: str = "首条任务领取区"
    home_tab_shape: str = "云梦试剑"


def _view_id(view: Any) -> int:
    value = getattr(view, "id", view)
    if value is None:
        raise RuntimeError("等待结果缺少场景编号")
    return int(value)


def claim_yunmeng_task_rewards_with_runtime(
    runtime: Any,
    snapshot: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    assets: YunmengTaskRewardGuiAssets,
    fast_reader: Callable[..., dict[str, Any]] = (
        read_selected_yunmeng_task_reward_fast_snapshot
    ),
    settle_seconds: float = 1.2,
) -> Generator[Any, None, dict[str, Any]]:
    """Execute the verified three-tab first-row-removal transaction.

    This function is complete but deliberately has no production asset
    instance yet.  Supplying one requires three real Yunmeng task-tab scenes;
    the exact shared prefab alone is not accepted as scene evidence.
    """

    selected_domain = str(snapshot.get("selected_domain") or "")
    authorized = [int(item) for item in plan.get("authorized_task_ids") or []]
    tabs = {
        str(key): [int(item) for item in value]
        for key, value in dict(plan.get("tabs") or {}).items()
    }
    active_tabs = [key for key, task_ids in tabs.items() if task_ids]
    missing = [
        key
        for key in active_tabs
        if key not in assets.task_scene_ids or key not in assets.tab_shape_names
    ]
    if missing:
        return {"ok": False, "reason": f"云梦任务页资产不完整: {missing[0]}"}
    if not selected_domain or not authorized or not active_tabs:
        return {"ok": False, "reason": "云梦 GUI 事务缺少严格授权"}

    target_scene_ids = list(dict.fromkeys(int(value) for value in assets.task_scene_ids.values()))
    current_view = yield from runtime.wait_click_then_view(
        assets.home_scene_id,
        assets.task_entry_shape,
        target_scene_ids,
        settle_seconds=settle_seconds,
        timeout=25.0,
        label="云梦试剑：进入任务页",
    )
    current_scene_id = _view_id(current_view)
    current_snapshot = dict(snapshot)
    claimed_now: list[int] = []

    for tab_key in active_tabs:
        target_scene_id = int(assets.task_scene_ids[tab_key])
        if current_scene_id != target_scene_id:
            current_view = yield from runtime.wait_click_then_view(
                current_scene_id,
                assets.tab_shape_names[tab_key],
                [target_scene_id],
                settle_seconds=0.8,
                timeout=20.0,
                label=f"云梦试剑：切换任务子页 {tab_key}",
            )
            current_scene_id = _view_id(current_view)

        remaining_in_tab = list(tabs[tab_key])
        while remaining_in_tab:
            expected = remaining_in_tab[0]
            before_authorized = [
                int(item)
                for item in current_snapshot.get("authorized_claim_task_ids") or []
            ]
            if expected not in before_authorized:
                return {"ok": False, "reason": f"taskId={expected} 已失去领取授权"}
            runtime.click_shape_center(current_scene_id, assets.first_row_claim_shape)
            yield from runtime.wait_action_settle(settle_seconds)
            after = fast_reader(
                selected_domain,
                expected_claimed_task_id=expected,
            )
            after_claimed = {int(item) for item in after.get("claimed_task_ids") or []}
            remaining_after = [
                int(item) for item in after.get("authorized_claim_task_ids") or []
            ]
            expected_remaining = [
                task_id for task_id in before_authorized if task_id != expected
            ]
            if (
                not after.get("ok")
                or not after.get("complete")
                or after.get("expected_task_claimed") is not True
                or expected not in after_claimed
                or remaining_after != expected_remaining
            ):
                return {
                    "ok": False,
                    "reason": f"taskId={expected} 点击后未形成精确单步状态迁移",
                }
            claimed_now.append(expected)
            remaining_in_tab.pop(0)
            current_snapshot = dict(after)

    home_view = yield from runtime.wait_click_then_view(
        current_scene_id,
        assets.home_tab_shape,
        [assets.home_scene_id],
        settle_seconds=0.8,
        timeout=20.0,
        label="云梦试剑：任务领取后返回活动主页",
    )
    if _view_id(home_view) != int(assets.home_scene_id):
        return {"ok": False, "reason": "任务领取后未返回云梦主页"}
    return {
        "ok": True,
        "claimed_task_ids": claimed_now,
        "after_snapshot": current_snapshot,
    }


def claim_yunmeng_task_rewards_if_available(
    *,
    reader: YunmengTaskRewardReader = read_yunmeng_task_reward_snapshot,
    adapter: YunmengTaskRewardGuiAdapter | None = None,
) -> Generator[Any, None, dict[str, Any]]:
    """Claim one complete live Yunmeng ladder or safely do nothing."""

    try:
        before = reader()
    except Exception as exc:
        return {"status": "fail_closed", "reason": f"领取前只读探针异常：{exc}"}
    plan = plan_yunmeng_task_reward_claim(before)
    if plan["status"] != "ready":
        return {
            "status": plan["status"],
            "reason": plan["reason"],
            "claimed_task_ids": [],
        }
    if adapter is None:
        return {
            "status": "pending_research",
            "reason": "缺少经过真实云梦任务页验收的 GUI 适配器",
            "claimed_task_ids": [],
            "authorized_task_ids": plan["authorized_task_ids"],
        }

    try:
        adapter_result = yield from adapter(before, plan)
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"GUI 局部事务异常：{exc}",
            "claimed_task_ids": [],
        }
    if not isinstance(adapter_result, dict) or not adapter_result.get("ok"):
        reason = (
            str(adapter_result.get("reason") or "GUI 局部事务未报告成功")
            if isinstance(adapter_result, dict)
            else "GUI 局部事务返回结构无效"
        )
        return {"status": "failed", "reason": reason, "claimed_task_ids": []}

    after = adapter_result.get("after_snapshot")
    if not isinstance(after, dict):
        try:
            after = reader()
        except Exception as exc:
            return {
                "status": "unverified",
                "reason": f"领取后只读复验异常：{exc}",
                "claimed_task_ids": [],
            }
    verification = verify_yunmeng_task_reward_transition(before, after)
    return {
        "status": "claimed" if verification["ok"] else "unverified",
        "reason": verification["reason"],
        "claimed_task_ids": verification["newly_claimed_task_ids"],
        "selected_domain": verification["selected_domain"],
    }
