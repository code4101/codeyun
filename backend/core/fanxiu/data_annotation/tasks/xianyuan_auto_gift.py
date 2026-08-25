from __future__ import annotations

"""Read-only planning boundary for the independent Xianyuan gift Job."""

import threading
from collections.abc import Callable, Generator, Mapping
from datetime import datetime
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import (
    NPC_GIFT_EXTERNAL_ROUTE,
    StorageBagAutoClaimDecision,
    decide_storage_bag_auto_claim_item,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    build_storage_bag_catalog_snapshot,
)
from backend.core.fanxiu.instrumentation.xianyuan_atlas import (
    read_xianyuan_atlas_runtime,
)
from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import (
    ensure_storage_bag_atlas_analysis,
    storage_bag_analysis_fingerprint,
)
from backend.db import engine


STANDARD_JOB_ID = "xianyuan-auto-gift"
TASK_TYPE = "xianyuan_auto_gift"

SnapshotReader = Callable[[], dict[str, Any]]
GiftGuiAdapter = Callable[
    [Any, dict[str, Any], dict[str, Any], threading.Event, dict[str, Any]],
    Generator[Any, Any, dict[str, Any]],
]


def read_selected_storage_gifts_runtime() -> dict[str, Any]:
    """Project current read-only backpack Runtime rows with persisted user flags."""

    runtime_snapshot = fanxiu_instrumentation_service.backpack_ui_snapshot()
    cards_by_id = load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]
    atlas = build_storage_bag_catalog_snapshot(
        runtime_snapshot,
        cards_by_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    with Session(engine) as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()
        return apply_storage_bag_item_settings(session, atlas)


def routed_npc_gift_items(
    storage_snapshot: Mapping[str, Any],
) -> list[StorageBagAutoClaimDecision]:
    """Select only the route explicitly owned by this Job."""

    if storage_snapshot.get("complete") is not True:
        raise RuntimeError("仙缘_自动送礼：储物袋 Runtime 清单不完整")
    result: list[StorageBagAutoClaimDecision] = []
    for row in storage_snapshot.get("items") or []:
        if not isinstance(row, Mapping):
            continue
        if row.get("auto_claim") is True and (
            str(row.get("analysis_fingerprint") or "")
            != storage_bag_analysis_fingerprint(row)
        ):
            raise RuntimeError(
                f"仙缘_自动送礼：礼物 {int(row.get('base_id') or 0)} 的持久化分类已过期"
            )
        decision = decide_storage_bag_auto_claim_item(row)
        if (
            decision is not None
            and decision.action == "route"
            and decision.external_route == NPC_GIFT_EXTERNAL_ROUTE
        ):
            result.append(decision)
    return result


def build_xianyuan_auto_gift_plan(
    routes: list[StorageBagAutoClaimDecision],
    xianyuan_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Map gift item IDs to native Xianyuan ``npc_id/runtime_index`` identities."""

    if xianyuan_snapshot.get("runtime_complete") is not True:
        raise RuntimeError("仙缘_自动送礼：仙缘 Runtime 对象清单不完整")

    people = [
        person
        for person in (xianyuan_snapshot.get("people") or [])
        if isinstance(person, Mapping)
    ]
    native_identities: set[tuple[int, int]] = set()
    normalized_people: list[tuple[Mapping[str, Any], int, int]] = []
    for person in people:
        npc_id = int(person.get("npc_id") or 0)
        raw_runtime_index = person.get("runtime_index")
        if npc_id <= 0 or not isinstance(raw_runtime_index, int) or raw_runtime_index < 0:
            continue
        identity = (npc_id, raw_runtime_index)
        if identity in native_identities:
            raise RuntimeError(f"仙缘_自动送礼：Runtime 对象身份重复 {identity}")
        native_identities.add(identity)
        normalized_people.append((person, npc_id, raw_runtime_index))

    items: list[dict[str, Any]] = []
    for route in routes:
        targets: list[dict[str, Any]] = []
        for person, npc_id, runtime_index in normalized_people:
            if person.get("giftable") is not True or person.get("hostile") is True:
                continue
            matching_options = [
                option
                for option in (person.get("gift_options") or [])
                if isinstance(option, Mapping)
                and int(option.get("item_id") or 0) == route.base_id
            ]
            if len(matching_options) > 1:
                raise RuntimeError(
                    f"仙缘_自动送礼：NPC {npc_id} 对礼物 {route.base_id} 存在重复配置映射"
                )
            if not matching_options:
                continue
            option = matching_options[0]
            targets.append({
                "npc_id": npc_id,
                "runtime_index": runtime_index,
                "name": str(person.get("name") or f"仙缘 {npc_id}"),
                "favor": int(person.get("favor") or 0),
                "favor_level": int(person.get("favor_level") or 0),
                "gift_item_id": route.base_id,
                "favorability_per_item": int(option.get("favorability") or 0),
                "career_conditional": bool(option.get("career_conditional")),
                "activity_gift": bool(option.get("activity_gift")),
            })
        if not targets:
            raise RuntimeError(
                f"仙缘_自动送礼：礼物 {route.base_id} 未映射到可送礼 Runtime 对象"
            )
        items.append({
            "base_id": route.base_id,
            "quantity": route.quantity,
            "note": route.note,
            "eligible_targets": targets,
        })

    return {
        "task_type": TASK_TYPE,
        "source_route": NPC_GIFT_EXTERNAL_ROUTE,
        "item_count": len(items),
        "items": items,
    }


def execute_xianyuan_auto_gift_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    storage_reader: SnapshotReader = read_selected_storage_gifts_runtime,
    xianyuan_reader: SnapshotReader = read_xianyuan_atlas_runtime,
    gui_adapter: GiftGuiAdapter | None = None,
):
    """Plan from read-only identities and refuse action without a formal adapter."""

    routes = routed_npc_gift_items(storage_reader())
    if not routes:
        return {
            "ok": True,
            "outcome": "complete",
            "reason": "没有已勾选且路由为 NPC 礼物的现有库存",
            "plan": {"task_type": TASK_TYPE, "source_route": NPC_GIFT_EXTERNAL_ROUTE, "item_count": 0, "items": []},
        }
    plan = build_xianyuan_auto_gift_plan(routes, xianyuan_reader())
    if gui_adapter is None:
        raise RuntimeError(
            "仙缘_自动送礼：已生成基于 npc_id/runtime_index 的送礼计划，"
            "但正式送礼 GUI adapter 尚未就绪；拒绝操作游戏或猜测坐标"
        )
    result = yield from gui_adapter(runner, ctx, payload, stop_event, plan)
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("verified") is not True:
        raise RuntimeError("仙缘_自动送礼：GUI adapter 未返回经过 Runtime 复验的成功终态")
    return {"ok": True, "outcome": "complete", "plan": plan, "action_result": result}


__all__ = [
    "STANDARD_JOB_ID",
    "TASK_TYPE",
    "build_xianyuan_auto_gift_plan",
    "execute_xianyuan_auto_gift_task",
    "read_selected_storage_gifts_runtime",
    "routed_npc_gift_items",
]
