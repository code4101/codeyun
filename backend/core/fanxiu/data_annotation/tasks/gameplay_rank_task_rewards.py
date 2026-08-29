from __future__ import annotations

"""Shared multi-tab reward transaction for gameplay-ranking activities."""

from collections.abc import Callable, Generator, Sequence
from dataclasses import dataclass
from typing import Any


TaskRewardReader = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class GameplayRankTaskTab:
    key: str
    subtype: int
    scene_id: int
    tab_shape: str


@dataclass(frozen=True)
class GameplayRankTaskAssets:
    activity_label: str
    home_scene_id: int
    tabs: Sequence[GameplayRankTaskTab]
    task_entry_shape: str = "任务"
    first_row_claim_shape: str = "首条任务领取区"
    home_shape: str = "天地弈局"


def _view_id(view: Any) -> int:
    value = getattr(view, "id", view)
    if value is None:
        raise RuntimeError("玩法榜任务页等待结果缺少场景编号")
    return int(value)


def _return_home(
    runtime: Any,
    current_scene_id: int,
    *,
    assets: GameplayRankTaskAssets,
    label: str,
) -> Generator[Any, None, None]:
    runtime.click_shape_center(current_scene_id, assets.home_shape)
    yield from runtime.wait_action_settle(0.8)


def claim_gameplay_rank_task_tabs(
    runtime: Any,
    *,
    assets: GameplayRankTaskAssets,
    reader: TaskRewardReader,
    settle_seconds: float = 1.2,
) -> Generator[Any, None, dict[str, Any]]:
    """Load task facts through the real page, then claim authorized rewards once."""

    tabs = tuple(assets.tabs)
    if not tabs:
        raise RuntimeError(f"{assets.activity_label}没有配置任务奖励页签")

    target_scenes = list(dict.fromkeys(int(tab.scene_id) for tab in tabs))
    current_view = yield from runtime.wait_click_then_view(
        int(assets.home_scene_id),
        assets.task_entry_shape,
        target_scenes,
        settle_seconds=settle_seconds,
        timeout=25.0,
        label=f"{assets.activity_label}：进入任务奖励页",
    )
    current_scene_id = _view_id(current_view)

    # Opening the task page naturally loads QuestMgr's activity rows.  Runtime
    # is the sole claim authority; no row click occurs before this full read.
    current_snapshot = dict(reader())
    if (
        not current_snapshot.get("ok")
        or not current_snapshot.get("available")
        or not current_snapshot.get("complete")
    ):
        yield from _return_home(
            runtime,
            current_scene_id,
            assets=assets,
            label=f"{assets.activity_label}：任务事实不完整，返回活动主页",
        )
        raise RuntimeError(f"{assets.activity_label}任务奖励 Runtime 事实不完整")

    subtype_by_id = {
        int(task_id): int(subtype)
        for task_id, subtype in dict(current_snapshot.get("task_subtypes") or {}).items()
    }
    authorized = [
        int(value) for value in current_snapshot.get("authorized_claim_task_ids") or []
    ]
    known_subtypes = {int(tab.subtype) for tab in tabs}
    unknown = [
        task_id
        for task_id in authorized
        if subtype_by_id.get(task_id) not in known_subtypes
    ]
    if unknown:
        yield from _return_home(
            runtime,
            current_scene_id,
            assets=assets,
            label=f"{assets.activity_label}：任务类型未知，返回活动主页",
        )
        raise RuntimeError(
            f"{assets.activity_label} taskId={unknown[0]} 未映射到已验证奖励页签"
        )

    if not authorized:
        yield from _return_home(
            runtime,
            current_scene_id,
            assets=assets,
            label=f"{assets.activity_label}：任务奖励已幂等完成，返回活动主页",
        )
        return {
            "ok": True,
            "idempotent": True,
            "visited_tabs": [],
            "claimed_task_ids": [],
            "after_snapshot": current_snapshot,
        }

    claimed_now: list[int] = []
    visited_tabs: list[str] = []

    for tab in tabs:
        target_scene_id = int(tab.scene_id)
        if current_scene_id != target_scene_id:
            current_view = yield from runtime.wait_click_then_view(
                current_scene_id,
                tab.tab_shape,
                [target_scene_id],
                settle_seconds=0.8,
                timeout=20.0,
                label=f"{assets.activity_label}：切换{tab.key}奖励页",
            )
            current_scene_id = _view_id(current_view)
        visited_tabs.append(str(tab.key))

        while True:
            before_authorized = [
                int(value)
                for value in current_snapshot.get("authorized_claim_task_ids") or []
            ]
            remaining_in_tab = [
                task_id
                for task_id in before_authorized
                if subtype_by_id.get(task_id) == int(tab.subtype)
            ]
            if not remaining_in_tab:
                break
            expected = remaining_in_tab[0]
            runtime.click_shape_center(current_scene_id, assets.first_row_claim_shape)
            yield from runtime.wait_action_settle(settle_seconds)
            after = reader(expected_claimed_task_id=expected)
            after_claimed = {int(value) for value in after.get("claimed_task_ids") or []}
            remaining_after = [
                int(value) for value in after.get("authorized_claim_task_ids") or []
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
                raise RuntimeError(
                    f"{assets.activity_label} taskId={expected} 点击后未形成精确单步状态迁移"
                )
            claimed_now.append(expected)
            current_snapshot = dict(after)

    yield from _return_home(
        runtime,
        current_scene_id,
        assets=assets,
        label=f"{assets.activity_label}：任务领取后返回活动主页",
    )
    return {
        "ok": True,
        "idempotent": False,
        "visited_tabs": visited_tabs,
        "claimed_task_ids": claimed_now,
        "after_snapshot": current_snapshot,
    }


__all__ = [
    "GameplayRankTaskAssets",
    "GameplayRankTaskTab",
    "claim_gameplay_rank_task_tabs",
]
