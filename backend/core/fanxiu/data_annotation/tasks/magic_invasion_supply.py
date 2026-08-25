from __future__ import annotations

"""Minimal deterministic Tianyan supply transaction for Magic Invasion."""

from dataclasses import asdict, dataclass
from math import ceil
import threading
from typing import Any, Mapping

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxGuiAdapter,
    StorageBagChoiceBoxRequest,
    choice_rewards_from_catalog,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    STORAGE_BAG_SCENE,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service


WORLD_SCENE = 34
TIANYAN_ITEM_ID = 1010004
MAGIC_RANKING_CHOICE_BOX_ID = 39031325


@dataclass(frozen=True)
class MagicSupplyRequest:
    instance_id: str
    owned_box_count: int
    open_box_count: int


@dataclass(frozen=True)
class MagicSupplyPlan:
    tianyan_before: int
    required_tianyan: int
    shortfall: int
    per_box: int
    required_box_count: int
    requests: tuple[MagicSupplyRequest, ...]

    @property
    def needed(self) -> bool:
        return self.required_box_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "requests": [asdict(item) for item in self.requests],
        }


def _runtime_items(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if (
        snapshot.get("complete") is not True
        or snapshot.get("source") != "active_backpack_panel_item_info_list"
    ):
        raise RuntimeError("魔道补给需要完整的当前储物袋 Runtime 快照")
    return [
        row
        for row in snapshot.get("items") or []
        if isinstance(row, Mapping) and not row.get("is_padding")
    ]


def _base_total(snapshot: Mapping[str, Any], base_id: int) -> int:
    return sum(
        max(0, int(row.get("num") or 0))
        for row in _runtime_items(snapshot)
        if int(row.get("base_id") or 0) == int(base_id)
    )


def build_magic_supply_plan(
    snapshot: Mapping[str, Any],
    catalog_cards_by_id: Mapping[str, Mapping[str, Any]],
    *,
    required_tianyan: int,
) -> MagicSupplyPlan:
    required = max(0, int(required_tianyan))
    current = _base_total(snapshot, TIANYAN_ITEM_ID)
    shortfall = max(0, required - current)
    if shortfall == 0:
        return MagicSupplyPlan(
            tianyan_before=current,
            required_tianyan=required,
            shortfall=0,
            per_box=0,
            required_box_count=0,
            requests=(),
        )
    box_card = catalog_cards_by_id.get(str(MAGIC_RANKING_CHOICE_BOX_ID)) or {}
    rewards = choice_rewards_from_catalog(box_card, catalog_cards_by_id)
    matches = [row for row in rewards if row.base_id == TIANYAN_ITEM_ID]
    if len(matches) != 1:
        raise RuntimeError("玩法榜甄选·魔道没有唯一的天眼符静态候选")
    per_box = int(matches[0].count_per_box)
    required_boxes = ceil(shortfall / per_box) if shortfall else 0
    instances = sorted(
        (
            row
            for row in _runtime_items(snapshot)
            if int(row.get("base_id") or 0) == MAGIC_RANKING_CHOICE_BOX_ID
            and int(row.get("num") or 0) > 0
            and str(row.get("instance_id") or "")
        ),
        key=lambda row: int(row.get("ui_index") or 0),
    )
    remaining = required_boxes
    requests: list[MagicSupplyRequest] = []
    for row in instances:
        if remaining <= 0:
            break
        owned = int(row.get("num") or 0)
        opened = min(owned, remaining)
        requests.append(
            MagicSupplyRequest(
                instance_id=str(row["instance_id"]),
                owned_box_count=owned,
                open_box_count=opened,
            )
        )
        remaining -= opened
    if remaining > 0:
        raise RuntimeError(
            f"天眼符缺口 {shortfall}，需要 {required_boxes} 个玩法榜甄选·魔道，"
            f"储物袋仅有 {required_boxes - remaining} 个"
        )
    return MagicSupplyPlan(
        tianyan_before=current,
        required_tianyan=required,
        shortfall=shortfall,
        per_box=per_box,
        required_box_count=required_boxes,
        requests=tuple(requests),
    )


def ensure_magic_tianyan_supply(
    runner: Any,
    ctx: dict[str, Any],
    stop_event: threading.Event,
    *,
    required_tianyan: int,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    catalog_reader=lambda: load_fanxiu_item_runtime_index(
        rebuild_missing=False
    )["cards_by_id"],
):
    """Open only the deterministic choice boxes needed to cover the shortfall."""

    runtime = runner._fanxiu_runtime(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    yield from runtime.goto_view(WORLD_SCENE)
    yield from runtime.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from runtime.wait_scene(
        STORAGE_BAG_SCENE,
        timeout=10.0,
        label="魔道入侵：等待储物袋补充天眼符",
    )
    before = dict(snapshot_reader())
    current_tianyan = _base_total(before, TIANYAN_ITEM_ID)
    required = max(0, int(required_tianyan))
    # An already sufficient backpack is authoritative by itself.  Loading the
    # choice-box catalog in that branch would create a needless availability
    # dependency even though no box action can be planned or executed.
    cards = dict(catalog_reader()) if current_tianyan < required else {}
    plan = build_magic_supply_plan(
        before,
        cards,
        required_tianyan=required,
    )
    box_name = str(
        (cards.get(str(MAGIC_RANKING_CHOICE_BOX_ID)) or {}).get("name") or ""
    ).strip()
    if plan.needed and not box_name:
        raise RuntimeError("玩法榜甄选·魔道缺少稳定 Catalog 名称")
    adapter = StorageBagChoiceBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards,
    )
    executions: list[dict[str, Any]] = []
    for request in plan.requests:
        execution = yield from adapter.execute(
            StorageBagChoiceBoxRequest(
                base_id=MAGIC_RANKING_CHOICE_BOX_ID,
                instance_id=request.instance_id,
                name=box_name,
                quantity=request.owned_box_count,
                note="选择天眼符",
                open_quantity=request.open_box_count,
            )
        )
        executions.append(
            {
                "instance_id": request.instance_id,
                "opened_count": execution.delta.opened_count,
                "reward_quantity": execution.delta.reward_quantity,
            }
        )
    after = dict(snapshot_reader())
    tianyan_after = _base_total(after, TIANYAN_ITEM_ID)
    expected_after = plan.tianyan_before + plan.required_box_count * plan.per_box
    if tianyan_after != expected_after or tianyan_after < plan.required_tianyan:
        raise RuntimeError(
            f"魔道补给后天眼符 {tianyan_after} != 精确期望 {expected_after}"
        )
    yield from runtime.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from runtime.wait_scene(
        WORLD_SCENE,
        timeout=10.0,
        label="魔道入侵：补给后返回世界",
    )
    return {
        "status": "supplied" if plan.needed else "sufficient",
        "plan": plan.to_dict(),
        "tianyan_after": tianyan_after,
        "executions": executions,
    }


__all__ = [
    "MAGIC_RANKING_CHOICE_BOX_ID",
    "TIANYAN_ITEM_ID",
    "MagicSupplyPlan",
    "MagicSupplyRequest",
    "build_magic_supply_plan",
    "ensure_magic_tianyan_supply",
]
