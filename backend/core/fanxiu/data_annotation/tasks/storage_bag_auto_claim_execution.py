from __future__ import annotations

"""Formal production orchestration for persisted storage-bag selections.

This module is the only bridge from the aggregate resource Job to the reusable
random/fixed/choice GUI adapters.  It refreshes the cumulative atlas from the
active Runtime list, persists one-time classification, validates the complete
batch before the first selected item is touched, and leaves NPC gifts to their
independent Xianyuan lifecycle.
"""

import threading
import time
from collections.abc import Callable, Generator, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy.exc import OperationalError
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
    USE_QUANTITY_SCENE,
    StorageBagFixedBoxGuiAdapter,
    StorageBagRandomBoxGuiAdapter,
    StorageBagRandomBoxRequest,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    sync_storage_bag_atlas,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import ensure_storage_bag_atlas_analysis
from backend.core.fanxiu.storage_bag_receipts import (
    persist_storage_bag_open_receipt,
    replay_storage_bag_open_receipts,
)
from backend.db import engine


def _wallet_snapshot_reader(currency_type: int) -> dict[str, Any]:
    """开箱前后的钱包读取发生在长时间 GUI 导航之后，进程缓存可能已经过期或换代。

    默认读者禁止重新发现进程，缓存未命中会以 process_cache_miss 失败关闭，把一次
    正常的缓存过期当成业务阻塞。本作业显式允许发现：它本来就要进入储物袋操作，
    不依赖复用只读巡检建立的进程缓存。
    """

    # 活动奖励币种在余额为 0 时会被客户端从钱包字典里省略，此时官方
    # GetCurrencyByType 的语义就是 0。开箱奖励里正有这类币种（2026-09-22 实测
    # 兑币类型 29907 尚未同步，动作前读取直接失败），所以显式采用客户端零值语义；
    # 缺失仍不会伪造成“已到账”——动作后如果还是读不到，差值就是 0，归因照样失败关闭。
    return read_wallet_currency_snapshot(
        currency_type, allow_discovery=True, missing_as_zero=True
    )


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


def persist_box_execution_after_verified_open(
    execution: Any,
    *,
    session_factory: Callable[[], Session],
) -> None:
    """Retry only a contended SQLite projection, never the game action.

    The box has already been consumed and its Runtime delta verified when this
    is called. A new DB session per attempt releases a failed transaction;
    the stable action key makes an uncertain commit safe to submit again.
    """

    for attempt in range(3):
        try:
            persist_storage_bag_open_receipt({
                "action_key": execution.action_key,
                "base_id": execution.request.base_id,
                "operation_template": execution.operation_template,
                "opened_count": execution.delta.opened_count,
                "rewards": list(execution.delta.rewards),
                "runtime_before_fingerprint": execution.delta.before_fingerprint,
                "runtime_after_fingerprint": execution.delta.after_fingerprint,
                "evidence": execution.evidence(),
            }, session_factory=session_factory)
            return
        except OperationalError as exc:
            locked = "database is locked" in str(exc).lower() or "database table is locked" in str(exc).lower()
            if not locked or attempt == 2:
                raise
            time.sleep(0.2 * (attempt + 1))

QUICK_OPERATION_UNSAFE_TEMPLATES = frozenset({"direct_use", "special_use"})
# Catalog type 48 is the spirit-ring equipment inventory. A verified opening
# of a second-tier ring box consumed the box but produced no BackpackPanel or
# wallet increase; the current yield projection cannot prove that destination.
UNOBSERVED_REWARD_CATALOG_TYPES = frozenset({48})

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
    cards_by_id: Mapping[str, Mapping[str, Any]],
    spirit_stone_direct_use_enabled: bool,
):
    """Keep unsupported selected items untouched without blocking safe adapters."""

    executable = []
    deferred = list(plan.deferred)
    for entry in plan.action_queue:
        if entry.template in {"open_random_box", "open_fixed_box"}:
            box_card = cards_by_id.get(str(entry.base_id)) or {}
            rewards = box_card.get("optional_gift_rewards") or []
            unobserved = [
                reward for reward in rewards
                if isinstance(reward, Mapping)
                and isinstance(cards_by_id.get(str(reward.get("id"))), Mapping)
                and cards_by_id[str(reward["id"])].get("type")
                in UNOBSERVED_REWARD_CATALOG_TYPES
            ]
            if unobserved:
                deferred.append(replace(
                    entry,
                    disposition="deferred",
                    reason=(
                        "候选奖励进入尚无 Runtime 增量读数的独立物品栏"
                        f"（Catalog 类型 {sorted(UNOBSERVED_REWARD_CATALOG_TYPES)}）；"
                        "本轮保留物品，不执行开箱"
                    ),
                ))
                continue
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
        cards_by_id=cards_by_id,
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
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    # #584 的出口标注只接回储物袋主页，导航图里没有从 #584 直达世界页的安全路径；
    # 上次失败若把现场留在数量确认弹窗上，必须先按已确认语义关掉它再导航，否则
    # go_scene 会直接触发场景修复闩锁（2026-09-22 实测）。
    scene = 0
    try:
        scene, _score, _frame = yield from context.current_scene()
    except Exception:
        scene = 0
    if int(scene or 0) == USE_QUANTITY_SCENE:
        yield from context.wait_click_then_scene(
            USE_QUANTITY_SCENE,
            "外侧空白",
            STORAGE_BAG_SCENE,
            # 关闭后场景识别会短暂停留在 #584（实测约 15s 才稳定回 #525），
            # 超时太短会把成功的关闭误判为失败。
            timeout=30.0,
            label="储物袋_操作/整单重入：关闭遗留 #584 数量确认",
        )
    yield from context.go_scene(WORLD_SCENE)
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene(
        [STORAGE_BAG_SCENE],
        wait=10.0,
        label="资源_自动使用/储物袋预检：等待储物袋主页",
    )
    before = dict(snapshot_reader())
    _cards_by_id, plan = _build_validated_production_plan(
        before,
        catalog_reader=catalog_reader,
        session_factory=session_factory,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )
    yield from context.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from context.wait_scene(
        [WORLD_SCENE],
        wait=10.0,
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

    replay_storage_bag_open_receipts(session_factory=session_factory)
    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    # 整单重入：上次失败可能把现场留在 #584 数量确认弹窗上，而导航图里没有从
    # #584 直达世界页的安全路径，直接 go_scene 会触发场景修复闩锁（2026-09-22
    # 实测）。先按已确认语义关掉弹窗再进储物袋。
    scene = 0
    try:
        scene, _score, _frame = yield from context.current_scene()
    except Exception:
        scene = 0
    if int(scene or 0) == USE_QUANTITY_SCENE:
        yield from context.wait_click_then_scene(
            USE_QUANTITY_SCENE,
            "外侧空白",
            STORAGE_BAG_SCENE,
            timeout=8.0,
            label="储物袋_操作/整单重入：关闭遗留 #584 数量确认",
        )
    yield from context.go_scene(WORLD_SCENE)
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene(
        [STORAGE_BAG_SCENE],
        wait=10.0,
        label="储物袋_操作/勾选物品：等待储物袋主页",
    )

    before = dict(snapshot_reader())
    cards_by_id, plan = _build_validated_production_plan(
        before,
        catalog_reader=catalog_reader,
        session_factory=session_factory,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )

    def recorder(execution) -> None:
        persist_box_execution_after_verified_open(
            execution,
            session_factory=session_factory,
        )

    random_adapter = StorageBagRandomBoxGuiAdapter(
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=_wallet_snapshot_reader,
    )
    fixed_adapter = StorageBagFixedBoxGuiAdapter(
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=_wallet_snapshot_reader,
    )
    choice_adapter = StorageBagChoiceBoxGuiAdapter(
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
    )
    spirit_stone_adapter = StorageBagSpiritStoneGuiAdapter(
        context=context,
        snapshot_reader=snapshot_reader,
        wallet_snapshot_reader=_wallet_snapshot_reader,
    )

    executions: list[dict[str, Any]] = []
    previous_box_adapter = None
    try:
        for entry in plan.action_queue:
            box_adapter = (
                random_adapter if entry.template == "open_random_box"
                else fixed_adapter if entry.template == "open_fixed_box"
                else None
            )
            if box_adapter is not previous_box_adapter:
                # Each box adapter caches its own post-open bag snapshot. A
                # different box, choice or direct-use action can consume other
                # items, so the old cache can no longer be the next pre-state.
                random_adapter.invalidate_reusable_snapshot()
                fixed_adapter.invalidate_reusable_snapshot()
            previous_box_adapter = box_adapter
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
        # The planning atlas describes the pre-action bag. Publish a complete
        # post-action observation while #525 is still open; never subtract
        # planned quantities or infer other reward inventory from clicks.
        after = dict(snapshot_reader())
        sync_storage_bag_atlas(
            after, cards_by_id,
            captured_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
        )
    except Exception:
        # 失败关闭也要退出中间页（#583 详情 / #584 数量确认）。留在弹窗上会让下一个
        # 到期作业直接拒绝动作，把队列一起带崩（2026-09-22 真实事故）。离场属于
        # best-effort：它只影响现场，不改变已发生的业务失败。
        try:
            scene, _score, _frame = yield from context.current_scene()
            if int(scene or 0) == USE_QUANTITY_SCENE:
                # #584 没有直达世界页的安全路径，先按已确认语义点掉弹窗。
                yield from context.wait_click_then_scene(
                    USE_QUANTITY_SCENE,
                    "外侧空白",
                    STORAGE_BAG_SCENE,
                    timeout=30.0,
                    label="储物袋_操作/失败离场：关闭 #584 数量确认",
                )
            yield from context.go_scene(WORLD_SCENE)
        except Exception:
            pass
        raise

    yield from context.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from context.wait_scene(
        [WORLD_SCENE],
        wait=10.0,
        label="储物袋_操作/勾选物品：返回世界",
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
        "final_runtime_fingerprint": after.get("fingerprint"),
        "executions": executions,
    }


__all__ = [
    "SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED",
    "SUPPORTED_PRODUCTION_TEMPLATES",
    "execute_storage_bag_auto_claim_task",
    "preflight_storage_bag_auto_claim_task",
]
