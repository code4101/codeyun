from __future__ import annotations

"""Pure planning for spending one Yaochi Flower Festival inventory."""

from copy import deepcopy
from typing import Any, Mapping

from pydantic import BaseModel

from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
    _first_supported_wujing_target,
)


def _snapshot_dict(snapshot: Mapping[str, Any] | BaseModel) -> dict[str, Any]:
    if isinstance(snapshot, BaseModel):
        return snapshot.model_dump(mode="python")
    return deepcopy(dict(snapshot))


def _require_complete(snapshot: Mapping[str, Any], *, name: str, field: str) -> None:
    if snapshot.get(field) is not True:
        detail = str(snapshot.get("runtime_error") or "").strip()
        suffix = f"：{detail}" if detail else ""
        raise ValueError(f"{name}快照不完整，拒绝规划仙花去向{suffix}")


def _flower_items_and_friendship(
    flower_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
    items: list[dict[str, Any]] = []
    total_count = 0
    total_friendship = 0
    for source in flower_snapshot.get("items") or []:
        if not isinstance(source, Mapping):
            raise ValueError("仙花资源快照包含无效条目，拒绝规划仙花去向")
        item = deepcopy(dict(source))
        count = item.get("count")
        friendship = item.get("friendship")
        if count is None or friendship is None:
            raise ValueError("仙花资源快照缺少数量或友好度，拒绝规划仙花去向")
        count = int(count)
        friendship = int(friendship)
        if count < 0 or friendship < 0:
            raise ValueError("仙花资源快照包含负数，拒绝规划仙花去向")
        if count <= 0:
            continue
        item["count"] = count
        item["friendship"] = friendship
        item["total_friendship"] = count * friendship
        items.append(item)
        total_count += count
        total_friendship += count * friendship
    return items, total_count, total_friendship


def plan_yaochi_flower_recipient(
    gongfa_snapshot: Mapping[str, Any] | BaseModel,
    xianyuan_snapshot: Mapping[str, Any] | BaseModel,
    flower_snapshot: Mapping[str, Any] | BaseModel,
) -> dict[str, Any]:
    """Choose one recipient for every owned activity flower.

    The caller must provide three complete snapshots.  This function performs
    no Runtime, database, GUI, or scheduler operation.  It intentionally does
    not split the inventory or cap spending at a reward boundary: one activity
    sends all currently owned flowers to the single best recipient selected by
    the existing GongFa-to-Wujing recommendation model.
    """

    gongfa = _snapshot_dict(gongfa_snapshot)
    xianyuan = _snapshot_dict(xianyuan_snapshot)
    flowers = _snapshot_dict(flower_snapshot)
    _require_complete(gongfa, name="功法图鉴", field="runtime_complete")
    _require_complete(xianyuan, name="仙缘图鉴", field="runtime_complete")
    _require_complete(flowers, name="仙花资源", field="complete")

    books = [
        deepcopy(dict(book))
        for book in gongfa.get("books") or []
        if isinstance(book, Mapping)
    ]
    people = [
        deepcopy(dict(person))
        for person in xianyuan.get("people") or []
        if isinstance(person, Mapping)
    ]
    if not books:
        raise ValueError("功法图鉴没有功法数据，拒绝规划仙花去向")
    if not people:
        raise ValueError("仙缘图鉴没有人物数据，拒绝规划仙花去向")

    target, projected_people, recommendation = _first_supported_wujing_target(
        people,
        books,
    )
    if target is None or recommendation is None:
        raise ValueError("当前功法优先序中没有可由仙缘悟境奖励推进的仙品或神品功法")

    npc_id = int(recommendation.get("npc_id") or 0)
    chosen = next(
        (
            person
            for person in projected_people
            if int(person.get("npc_id") or 0) == npc_id
        ),
        None,
    )
    if chosen is None:
        raise ValueError("仙缘推荐结果未映射到 Runtime 人物，拒绝规划仙花去向")

    flower_items, total_count, total_friendship = _flower_items_and_friendship(flowers)
    return {
        "status": "ready",
        "strategy": "single_recipient_all_flowers",
        "chosen_npc": {
            "npc_id": npc_id,
            "runtime_index": chosen.get("runtime_index"),
            "name": str(chosen.get("name") or recommendation.get("name") or npc_id),
        },
        "target_gongfa": deepcopy(target),
        "recommendation": deepcopy(recommendation),
        "flower_items": flower_items,
        "total_flower_count": total_count,
        "total_friendship": total_friendship,
        "ignore_reward_overflow": True,
        "recipient_count": 1,
    }


__all__ = ["plan_yaochi_flower_recipient"]
