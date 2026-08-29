from __future__ import annotations

"""Formal production orchestration for persisted storage-bag selections.

This module is the only bridge from the aggregate resource Job to the reusable
random/fixed/choice GUI adapters.  It refreshes the cumulative atlas from the
active Runtime list, persists one-time classification, validates the complete
batch before the first selected item is touched, and leaves NPC gifts to their
independent Xianyuan lifecycle.
"""

import threading
from collections.abc import Callable, Generator, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlmodel import Session

from backend.core.fanxiu.catalog.item import load_fanxiu_item_runtime_index
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_plan import (
    StorageBagAutoClaimBlocked,
    StorageBagAutoClaimEntry,
    build_storage_bag_auto_claim_plan,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxGuiAdapter,
    StorageBagChoiceBoxRequest,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_direct_use import (
    SPIRIT_STONE_BASE_ID,
    SPIRIT_STONE_NAME,
    StorageBagDirectUseRequest,
    StorageBagSpiritStoneGuiAdapter,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_random_box import (
    STORAGE_BAG_SCENE,
    StorageBagFixedBoxGuiAdapter,
    StorageBagRandomBoxGuiAdapter,
    StorageBagRandomBoxRequest,
    record_box_execution,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    sync_storage_bag_atlas,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import ensure_storage_bag_atlas_analysis
from backend.db import engine


WORLD_SCENE = 34
SUPPORTED_PRODUCTION_TEMPLATES = frozenset({
    "open_random_box",
    "open_fixed_box",
    "choice_box",
})
# The implementation is wired below, but the standard Job must remain closed
# until the final #584 click and its exact bag/wallet delta have been verified
# in one real Runtime transaction.  Research Cells may opt in explicitly.
SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED = False

QUICK_OPERATION_UNSAFE_TEMPLATES = frozenset({"direct_use", "special_use"})

SnapshotReader = Callable[[], Mapping[str, Any]]
CatalogReader = Callable[[], Mapping[str, Mapping[str, Any]]]
SessionFactory = Callable[[], Session]


def _default_catalog_reader() -> Mapping[str, Mapping[str, Any]]:
    return load_fanxiu_item_runtime_index(rebuild_missing=False)["cards_by_id"]


def _default_session_factory() -> Session:
    return Session(engine)


def _plan_failure_reason(plan) -> str:
    return "; ".join(f"{entry.base_id}:{entry.reason}" for entry in plan.failures)


def _is_enabled_spirit_stone_entry(
    entry: StorageBagAutoClaimEntry,
    *,
    spirit_stone_direct_use_enabled: bool,
) -> bool:
    """Authorize exactly one direct-use product, never the whole template."""

    return (
        spirit_stone_direct_use_enabled
        and entry.template == "direct_use"
        and entry.base_id == SPIRIT_STONE_BASE_ID
        and entry.name.strip() == SPIRIT_STONE_NAME
        and bool(entry.instance_id)
        and entry.quantity > 0
    )


def _validate_production_batch(
    plan,
    *,
    spirit_stone_direct_use_enabled: bool = (
        SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED
    ),
) -> None:
    """Reject the complete selected batch before the first irreversible use."""

    if plan.failures:
        raise StorageBagAutoClaimBlocked(
            f"储物袋勾选计划含失败项：{_plan_failure_reason(plan)}"
        )
    unsupported_totals: dict[tuple[str, int, str], int] = {}
    for entry in plan.action_queue:
        if entry.template in SUPPORTED_PRODUCTION_TEMPLATES or (
            _is_enabled_spirit_stone_entry(
                entry,
                spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
            )
        ):
            continue
        key = (str(entry.template), int(entry.base_id), str(entry.name))
        unsupported_totals[key] = unsupported_totals.get(key, 0) + int(entry.quantity)
    unsupported = [
        f"{template}[{base_id} {name} ×{quantity}]"
        for (template, base_id, name), quantity in sorted(unsupported_totals.items())
    ]
    if unsupported:
        raise StorageBagAutoClaimBlocked(
            f"储物袋勾选计划尚无正式生产适配器：{'; '.join(unsupported)}"
        )
    invalid_routes = [
        entry
        for entry in plan.routed
        if entry.external_route != "xianyuan_auto_gift"
    ]
    if invalid_routes:
        raise StorageBagAutoClaimBlocked("储物袋勾选计划包含未知外部业务路由")


def _defer_unadapted_production_entries(
    plan,
    *,
    spirit_stone_direct_use_enabled: bool,
):
    """Keep unsupported selected items untouched without blocking safe adapters."""

    executable = []
    deferred = list(plan.deferred)
    for entry in plan.action_queue:
        if entry.template in SUPPORTED_PRODUCTION_TEMPLATES or (
            _is_enabled_spirit_stone_entry(
                entry,
                spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
            )
        ):
            executable.append(entry)
            continue
        deferred.append(replace(
            entry,
            disposition="deferred",
            reason=(
                "尚无已完成真实验收的正式生产适配器；"
                "本轮失败关闭并保持物品未消费"
            ),
        ))
    return replace(
        plan,
        action_queue=tuple(executable),
        deferred=tuple(deferred),
    )


def _quick_operation_blockers(plan) -> tuple[StorageBagAutoClaimEntry, ...]:
    """Return present deferred items that broad ``Use=ON`` could consume."""

    return tuple(
        entry
        for entry in plan.deferred
        if entry.quantity > 0 and entry.template in QUICK_OPERATION_UNSAFE_TEMPLATES
    )


def _blocker_records(plan) -> list[dict[str, Any]]:
    return [
        {
            "base_id": entry.base_id,
            "name": entry.name,
            "template": entry.template,
            "quantity": entry.quantity,
            "reason": entry.reason,
        }
        for entry in _quick_operation_blockers(plan)
    ]


def _random_request(entry: StorageBagAutoClaimEntry) -> StorageBagRandomBoxRequest:
    if entry.instance_id is None:
        raise StorageBagAutoClaimBlocked(f"储物袋物品 {entry.base_id} 缺少 Runtime instance_id")
    return StorageBagRandomBoxRequest(
        base_id=entry.base_id,
        instance_id=entry.instance_id,
        name=entry.name,
        quantity=entry.quantity,
    )


def _choice_request(entry: StorageBagAutoClaimEntry) -> StorageBagChoiceBoxRequest:
    if entry.instance_id is None:
        raise StorageBagAutoClaimBlocked(f"储物袋物品 {entry.base_id} 缺少 Runtime instance_id")
    return StorageBagChoiceBoxRequest(
        base_id=entry.base_id,
        instance_id=entry.instance_id,
        name=entry.name,
        quantity=entry.quantity,
        note=entry.note,
    )


def _direct_use_request(entry: StorageBagAutoClaimEntry) -> StorageBagDirectUseRequest:
    if not _is_enabled_spirit_stone_entry(
        entry,
        spirit_stone_direct_use_enabled=True,
    ):
        raise StorageBagAutoClaimBlocked(
            "储物袋直接使用仅支持 base1001 灵石的唯一 Runtime 实例"
        )
    return StorageBagDirectUseRequest(
        base_id=entry.base_id,
        instance_id=str(entry.instance_id),
        name=entry.name,
        quantity=entry.quantity,
    )


def _build_validated_production_plan(
    before: Mapping[str, Any],
    *,
    catalog_reader: CatalogReader,
    session_factory: SessionFactory,
    spirit_stone_direct_use_enabled: bool = (
        SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED
    ),
):
    """Refresh policy metadata and reject the whole selected batch."""

    cards_by_id = dict(catalog_reader())
    atlas = sync_storage_bag_atlas(
        before,
        cards_by_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    with session_factory() as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()
        projected = apply_storage_bag_item_settings(session, atlas)
    plan = build_storage_bag_auto_claim_plan(projected, before)
    plan = _defer_unadapted_production_entries(
        plan,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )
    _validate_production_batch(
        plan,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )
    return cards_by_id, plan


def preflight_storage_bag_auto_claim_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    snapshot_reader: SnapshotReader = fanxiu_instrumentation_service.backpack_ui_snapshot,
    catalog_reader: CatalogReader = _default_catalog_reader,
    session_factory: SessionFactory = _default_session_factory,
    spirit_stone_direct_use_enabled: bool = (
        SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED
    ),
) -> Generator[Any, Any, dict[str, Any]]:
    """Prove the selected batch is supported before any aggregate mutation."""

    del payload
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
        label="资源_自动使用/储物袋预检：等待储物袋主页",
    )
    before = dict(snapshot_reader())
    _cards_by_id, plan = _build_validated_production_plan(
        before,
        catalog_reader=catalog_reader,
        session_factory=session_factory,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )
    yield from runtime.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from runtime.wait_scene(
        WORLD_SCENE,
        timeout=10.0,
        label="资源_自动使用/储物袋预检：返回世界",
    )
    return {
        "ok": True,
        "outcome": "ready",
        "selected_base_count": plan.selected_base_count,
        "action_count": len(plan.action_queue),
        "routed_count": len(plan.routed),
        "deferred_count": len(plan.deferred),
        "quick_operation_allowed": not _quick_operation_blockers(plan),
        "quick_operation_blockers": _blocker_records(plan),
        "runtime_fingerprint": plan.runtime_fingerprint,
    }


def execute_storage_bag_auto_claim_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    snapshot_reader: SnapshotReader = fanxiu_instrumentation_service.backpack_ui_snapshot,
    catalog_reader: CatalogReader = _default_catalog_reader,
    session_factory: SessionFactory = _default_session_factory,
    spirit_stone_direct_use_enabled: bool = (
        SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED
    ),
) -> Generator[Any, Any, dict[str, Any]]:
    """Execute the current persisted selection through reusable UI families."""

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
        label="资源_自动使用/储物袋勾选：等待储物袋主页",
    )

    before = dict(snapshot_reader())
    cards_by_id, plan = _build_validated_production_plan(
        before,
        catalog_reader=catalog_reader,
        session_factory=session_factory,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )

    def recorder(execution) -> None:
        with session_factory() as session:
            record_box_execution(session, execution)
            session.commit()

    random_adapter = StorageBagRandomBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=read_wallet_currency_snapshot,
    )
    fixed_adapter = StorageBagFixedBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=read_wallet_currency_snapshot,
    )
    choice_adapter = StorageBagChoiceBoxGuiAdapter(
        runtime=runtime,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
    )
    spirit_stone_adapter = StorageBagSpiritStoneGuiAdapter(
        runtime=runtime,
        snapshot_reader=snapshot_reader,
        wallet_snapshot_reader=read_wallet_currency_snapshot,
    )

    executions: list[dict[str, Any]] = []
    for entry in plan.action_queue:
        if entry.template == "open_random_box":
            result = yield from random_adapter.execute(_random_request(entry))
        elif entry.template == "open_fixed_box":
            result = yield from fixed_adapter.execute(_random_request(entry))
        elif entry.template == "choice_box":
            result = yield from choice_adapter.execute(_choice_request(entry))
        elif _is_enabled_spirit_stone_entry(
            entry,
            spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
        ):
            result = yield from spirit_stone_adapter.execute(
                _direct_use_request(entry)
            )
        else:  # guarded by _validate_production_batch
            raise AssertionError(f"unreachable storage-bag template: {entry.template}")
        executions.append({
            "base_id": entry.base_id,
            "instance_id": entry.instance_id,
            "template": entry.template,
            "verified": result is not None,
        })

    yield from runtime.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from runtime.wait_scene(
        WORLD_SCENE,
        timeout=10.0,
        label="资源_自动使用/储物袋勾选：返回世界",
    )
    return {
        "ok": True,
        "outcome": "complete",
        "selected_base_count": plan.selected_base_count,
        "executed_count": len(executions),
        "routed_count": len(plan.routed),
        "deferred_count": len(plan.deferred),
        "quick_operation_blockers": _blocker_records(plan),
        "runtime_fingerprint": plan.runtime_fingerprint,
        "executions": executions,
    }


__all__ = [
    "SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED",
    "SUPPORTED_PRODUCTION_TEMPLATES",
    "execute_storage_bag_auto_claim_task",
    "preflight_storage_bag_auto_claim_task",
]
