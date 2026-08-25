from __future__ import annotations

"""Read-only planning for user-selected storage-bag item operations.

The planner joins three independent facts without performing a GUI action:

* the cumulative atlas owns stable item metadata and user-facing order;
* the database owns ``auto_claim`` and the user's note;
* the live backpack Runtime owns current instances, quantities and UI order.

The resulting queue is deliberately adapter-oriented.  A later executor must
provide one reusable adapter per UI family; missing adapters are rejected for
the whole batch before the first item is touched.
"""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from sqlmodel import Session

from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    load_storage_bag_atlas,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_policy import (
    decide_storage_bag_auto_claim_item,
)
from backend.core.fanxiu.storage_bag_settings import (
    apply_storage_bag_item_settings,
)
from backend.core.fanxiu.storage_bag_usage import (
    ensure_storage_bag_atlas_analysis,
    storage_bag_analysis_fingerprint,
)


StorageBagTemplate = Literal[
    "open_random_box",
    "open_fixed_box",
    "choice_box",
    "direct_use",
    "special_use",
]
StorageBagDisposition = Literal["action", "routed", "deferred", "failed"]

_ADAPTER_TEMPLATE_BY_ANALYSIS = {
    "random_box": "open_random_box",
    "fixed_box": "open_fixed_box",
    "choice_box": "choice_box",
    "direct_use": "direct_use",
    "special_use": "special_use",
}


@dataclass(frozen=True)
class StorageBagAutoClaimEntry:
    base_id: int
    instance_id: str | None
    name: str
    quantity: int
    atlas_order: int
    runtime_ui_index: int | None
    disposition: StorageBagDisposition
    reason: str
    template: StorageBagTemplate | None = None
    reward_mode: str = ""
    note: str = ""
    external_route: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StorageBagAutoClaimPlan:
    runtime_fingerprint: str
    selected_base_count: int
    action_queue: tuple[StorageBagAutoClaimEntry, ...]
    routed: tuple[StorageBagAutoClaimEntry, ...]
    deferred: tuple[StorageBagAutoClaimEntry, ...]
    failures: tuple[StorageBagAutoClaimEntry, ...]

    @property
    def ready(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "runtime_fingerprint": self.runtime_fingerprint,
            "selected_base_count": self.selected_base_count,
            "action_queue": [entry.to_dict() for entry in self.action_queue],
            "routed": [entry.to_dict() for entry in self.routed],
            "deferred": [entry.to_dict() for entry in self.deferred],
            "failures": [entry.to_dict() for entry in self.failures],
        }


class StorageBagAutoClaimBlocked(RuntimeError):
    """Raised before any adapter runs when a selected batch is not executable."""


def _item_metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    item = row.get("item")
    return item if isinstance(item, Mapping) else {}


def _runtime_items(snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if snapshot.get("complete") is not True:
        raise ValueError("储物袋 Runtime 快照不完整")
    if snapshot.get("source") != "active_backpack_panel_item_info_list":
        raise ValueError("储物袋 Runtime 快照不是当前活动面板 ItemInfoList")
    items = snapshot.get("items")
    if not isinstance(items, list):
        raise ValueError("储物袋 Runtime 快照缺少 items")
    result = tuple(
        item
        for item in items
        if isinstance(item, Mapping) and not item.get("is_padding")
    )
    previous_ui_index = -1
    for item in result:
        ui_index = item.get("ui_index")
        if not isinstance(ui_index, int) or ui_index <= previous_ui_index:
            raise ValueError("储物袋 Runtime 实例缺少严格递增 ui_index")
        previous_ui_index = ui_index
        if not isinstance(item.get("base_id"), int) or not isinstance(item.get("num"), int):
            raise ValueError("储物袋 Runtime 实例缺少 base_id/num")
    return result


def build_storage_bag_auto_claim_plan(
    atlas: Mapping[str, Any],
    runtime_snapshot: Mapping[str, Any],
) -> StorageBagAutoClaimPlan:
    """Build a unique-instance physical queue without touching the game."""

    runtime_items = _runtime_items(runtime_snapshot)
    runtime_by_base: dict[int, list[Mapping[str, Any]]] = {}
    for item in runtime_items:
        runtime_by_base.setdefault(int(item["base_id"]), []).append(item)

    selected = sorted(
        (
            row
            for row in atlas.get("items") or []
            if isinstance(row, Mapping) and row.get("auto_claim") is True
        ),
        key=lambda row: (int(row.get("atlas_order") or 0), int(row.get("base_id") or 0)),
    )
    actions: list[StorageBagAutoClaimEntry] = []
    routed: list[StorageBagAutoClaimEntry] = []
    deferred: list[StorageBagAutoClaimEntry] = []
    failures: list[StorageBagAutoClaimEntry] = []
    seen_base_ids: set[int] = set()
    seen_instance_ids: set[str] = set()

    def add(entry: StorageBagAutoClaimEntry) -> None:
        {
            "action": actions,
            "routed": routed,
            "deferred": deferred,
            "failed": failures,
        }[entry.disposition].append(entry)

    for row in selected:
        base_id = int(row.get("base_id") or 0)
        atlas_order = int(row.get("atlas_order") or 0)
        item = _item_metadata(row)
        name = str(item.get("name") or "").strip()
        note = str(row.get("note") or "").strip()
        if base_id <= 0 or base_id in seen_base_ids:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=0,
                    atlas_order=atlas_order,
                    runtime_ui_index=None,
                    disposition="failed",
                    reason="勾选图鉴中 base_id 无效或重复",
                    note=note,
                )
            )
            continue
        seen_base_ids.add(base_id)
        if not name:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name="",
                    quantity=0,
                    atlas_order=atlas_order,
                    runtime_ui_index=None,
                    disposition="failed",
                    reason="图鉴缺少稳定 Catalog 名称，不能核验详情",
                    note=note,
                )
            )
            continue

        instances = runtime_by_base.get(base_id, [])
        quantity_total = sum(max(0, int(instance["num"])) for instance in instances)
        expected_fingerprint = storage_bag_analysis_fingerprint(row)
        if str(row.get("analysis_fingerprint") or "") != expected_fingerprint:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=quantity_total,
                    atlas_order=atlas_order,
                    runtime_ui_index=(int(instances[0]["ui_index"]) if instances else None),
                    disposition="failed",
                    reason="持久化分类指纹缺失或已过期，必须先刷新一次性分类",
                    note=note,
                )
            )
            continue
        if not instances:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=0,
                    atlas_order=atlas_order,
                    runtime_ui_index=None,
                    disposition="deferred",
                    reason="当前完整 Runtime 中没有该图鉴物品",
                    reward_mode=str(row.get("yield_mode") or ""),
                    note=note,
                )
            )
            continue
        decision = decide_storage_bag_auto_claim_item({**row, "num": quantity_total})
        if decision is None:
            continue
        template = _ADAPTER_TEMPLATE_BY_ANALYSIS.get(decision.operation_template)
        if decision.action == "route":
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=quantity_total,
                    atlas_order=atlas_order,
                    runtime_ui_index=int(instances[0]["ui_index"]),
                    disposition="routed",
                    reason=decision.reason,
                    reward_mode=decision.yield_mode,
                    note=note,
                    external_route=decision.external_route,
                )
            )
            continue
        if decision.action in {"fail", "defer"}:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=quantity_total,
                    atlas_order=atlas_order,
                    runtime_ui_index=int(instances[0]["ui_index"]),
                    disposition="failed" if decision.action == "fail" else "deferred",
                    reason=decision.reason,
                    template=template,
                    reward_mode=decision.yield_mode,
                    note=note,
                )
            )
            continue
        if template is None:
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=None,
                    name=name,
                    quantity=quantity_total,
                    atlas_order=atlas_order,
                    runtime_ui_index=int(instances[0]["ui_index"]),
                    disposition="failed",
                    reason=f"持久化模板没有可复用 GUI adapter：{decision.operation_template}",
                    reward_mode=decision.yield_mode,
                    note=note,
                )
            )
            continue

        for instance in instances:
            instance_id = str(instance.get("instance_id") or "").strip()
            quantity = int(instance.get("num") or 0)
            ui_index = int(instance["ui_index"])
            if not instance_id or instance_id in seen_instance_ids:
                add(
                    StorageBagAutoClaimEntry(
                        base_id=base_id,
                        instance_id=instance_id or None,
                        name=name,
                        quantity=quantity,
                        atlas_order=atlas_order,
                        runtime_ui_index=ui_index,
                        disposition="failed",
                        reason="Runtime instance_id 缺失或重复，不能形成唯一操作队列",
                        template=template,
                        reward_mode=decision.yield_mode,
                        note=note,
                    )
                )
                continue
            seen_instance_ids.add(instance_id)
            if quantity <= 0:
                add(
                    StorageBagAutoClaimEntry(
                        base_id=base_id,
                        instance_id=instance_id,
                        name=name,
                        quantity=quantity,
                        atlas_order=atlas_order,
                        runtime_ui_index=ui_index,
                        disposition="deferred",
                        reason="Runtime 实例数量不为正",
                        template=template,
                        reward_mode=decision.yield_mode,
                        note=note,
                    )
                )
                continue
            add(
                StorageBagAutoClaimEntry(
                    base_id=base_id,
                    instance_id=instance_id,
                    name=name,
                    quantity=quantity,
                    atlas_order=atlas_order,
                    runtime_ui_index=ui_index,
                    disposition="action",
                    reason=decision.reason,
                    template=template,
                    reward_mode=decision.yield_mode,
                    note=note,
                )
            )

    actions.sort(
        key=lambda entry: (
            entry.runtime_ui_index if entry.runtime_ui_index is not None else 10**9,
            entry.atlas_order,
            entry.base_id,
            entry.instance_id or "",
        )
    )
    return StorageBagAutoClaimPlan(
        runtime_fingerprint=str(runtime_snapshot.get("fingerprint") or ""),
        selected_base_count=len(selected),
        action_queue=tuple(actions),
        routed=tuple(routed),
        deferred=tuple(deferred),
        failures=tuple(failures),
    )


def load_storage_bag_auto_claim_plan(
    session: Session,
    runtime_snapshot: Mapping[str, Any],
    *,
    atlas_path: str | Path | None = None,
) -> StorageBagAutoClaimPlan:
    """Read the cumulative atlas plus DB settings and produce one live plan."""

    atlas = load_storage_bag_atlas(path=atlas_path)
    if atlas is None:
        raise ValueError("储物袋累计图鉴为空")
    # The formal Job must not depend on the user having opened the wiki first.
    # This is idempotent: unchanged immutable Catalog semantics keep the same
    # fingerprint and do not rewrite the classification.
    ensure_storage_bag_atlas_analysis(session, atlas)
    session.commit()
    return build_storage_bag_auto_claim_plan(
        apply_storage_bag_item_settings(session, atlas),
        runtime_snapshot,
    )


def dispatch_storage_bag_auto_claim_plan(
    plan: StorageBagAutoClaimPlan,
    adapters: Mapping[StorageBagTemplate, Callable[[StorageBagAutoClaimEntry], Any]],
) -> tuple[Any, ...]:
    """Dispatch a fully planned batch only when every adapter is available.

    Validation is intentionally all-or-nothing and runs before the first
    callback, so an unsupported item cannot leave a partially consumed bag.
    """

    if plan.failures:
        reasons = "; ".join(
            f"{entry.base_id}:{entry.reason}" for entry in plan.failures
        )
        raise StorageBagAutoClaimBlocked(f"储物袋勾选计划含失败项：{reasons}")
    missing = sorted(
        {
            str(entry.template)
            for entry in plan.action_queue
            if entry.template is not None and entry.template not in adapters
        }
    )
    if missing:
        raise StorageBagAutoClaimBlocked(
            f"储物袋勾选计划缺少 UI 适配器：{', '.join(missing)}"
        )
    return tuple(adapters[entry.template](entry) for entry in plan.action_queue if entry.template)


__all__ = [
    "StorageBagAutoClaimBlocked",
    "StorageBagAutoClaimEntry",
    "StorageBagAutoClaimPlan",
    "StorageBagDisposition",
    "StorageBagTemplate",
    "build_storage_bag_auto_claim_plan",
    "dispatch_storage_bag_auto_claim_plan",
    "load_storage_bag_auto_claim_plan",
]
