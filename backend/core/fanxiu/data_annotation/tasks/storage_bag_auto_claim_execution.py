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
    refresh_storage_bag_auto_claim_entry,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_choice_box import (
    StorageBagChoiceBoxGuiAdapter,
    StorageBagChoiceBoxRequest,
    record_storage_bag_choice_execution,
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
    plan_current_random_box_click,
)
from backend.core.fanxiu.instrumentation import fanxiu_instrumentation_service
from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
    sync_storage_bag_atlas,
)
from backend.core.fanxiu.instrumentation.wallet import read_wallet_currency_snapshot
from backend.core.fanxiu.instrumentation.backpack import read_equipment_gem_inventory_snapshot
from backend.core.fanxiu.storage_bag_settings import apply_storage_bag_item_settings
from backend.core.fanxiu.storage_bag_usage import ensure_storage_bag_atlas_analysis
from backend.core.fanxiu.storage_bag_receipts import (
    list_storage_bag_open_receipts,
    persist_storage_bag_open_receipt,
    replay_storage_bag_open_receipts,
)
from backend.db import engine
from backend.core.fanxiu.data_annotation.tasks.storage_bag_balanced_choice_execution import (
    storage_bag_balanced_profiles,execute_balanced_storage_bag_choice,
)
from .storage_bag_disciple_letter import (
    StorageBagDiscipleLetterGuiAdapter, is_disciple_letter_entry, finish_existing_disciple_travel,
    reconcile_pending_disciple_letters,
)


class StorageBagNavigationYield(RuntimeError):
    """A non-consuming locator boundary yields to a scheduled duty."""
    def __init__(self, admission):
        super().__init__('储物袋定位让出到期作业')
        self.admission = admission


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
# 2026-10-08: exact stack 165198 consumed; wallet 521891 -> 687089.
# The committed storage-bag-direct-use:b607c61c... receipt binds both deltas
# and the same process. Fresh production stacks use this verified adapter.
SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED = True
VIP_DIRECT_USE_PRODUCTION_ENABLED = True  # 2026-10-08 exact 8450-item receipt.
# Independent inventory receipts: 25 -> 24 / ring13040001 38 -> 39;
# 23 -> 22 / ring13600001 84 -> 85. Other equipment boxes stay closed.
RING_BOX_PRODUCTION_IDS = frozenset({19010195, 19010277})


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
# Type48 rewards require the independent equipment inventory adapter. Only
# individually accepted products may bypass the generic unsupported guard.
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


def _is_enabled_vip_entry(entry: StorageBagAutoClaimEntry) -> bool:
    """Only the independently accepted VIP1010 product joins direct-use."""
    return (VIP_DIRECT_USE_PRODUCTION_ENABLED and entry.template == "direct_use"
            and entry.base_id == 1010 and entry.name.strip() == "VIP经验"
            and bool(entry.instance_id) and entry.quantity > 0)


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
        if is_disciple_letter_entry(entry):
            continue
        if _is_enabled_vip_entry(entry):
            continue
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
        if is_disciple_letter_entry(entry):
            executable.append(entry)
            continue
        if _is_enabled_vip_entry(entry):
            executable.append(entry)
            continue
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
            if unobserved and not (entry.template == "open_random_box"
                                  and entry.base_id in RING_BOX_PRODUCTION_IDS):
                deferred.append(replace(
                    entry,
                    disposition="deferred",
                    completion_required=True,
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
            completion_required=True,
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


def remaining_storage_bag_current_items(
    plan, *, disciple_capacity_finished: bool = False,
) -> tuple[StorageBagAutoClaimEntry, ...]:
    """Current actionable stock, excluding this attempt's proven full capacity.

    A completed opening queue can create selected spirit stones or inner boxes.
    Only a fresh replan can prove closure. NPC routes and deferred policies are
    separate plan components; remaining letters are legitimate only after the
    native adapter actually proved their shared capacity full in this attempt.
    """
    return tuple(entry for entry in plan.action_queue
                 if not (disciple_capacity_finished and is_disciple_letter_entry(entry)))


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
    if not _is_enabled_vip_entry(entry) and not _is_enabled_spirit_stone_entry(
        entry,
        spirit_stone_direct_use_enabled=True,
    ):
        raise StorageBagAutoClaimBlocked(
            "储物袋直接使用仅支持已验收产品的唯一 Runtime 实例"
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
    from backend.core.fanxiu.instrumentation.storage_bag_catalog import (
        load_storage_bag_atlas, resolve_storage_bag_catalog_cards,
    )
    historical = load_storage_bag_atlas() or {'items': []}
    ids = [int(r['base_id']) for r in before.get('items', []) if not r.get('is_padding')]
    ids.extend(int(r['base_id']) for r in historical['items'] if not r.get('item'))
    cards_by_id = resolve_storage_bag_catalog_cards(cards_by_id, ids)
    atlas = sync_storage_bag_atlas(
        before,
        cards_by_id,
        captured_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    with session_factory() as session:
        ensure_storage_bag_atlas_analysis(session, atlas)
        session.commit()
        projected = apply_storage_bag_item_settings(session, atlas)
        from backend.core.fanxiu.storage_bag_unknown_research import sync_storage_bag_unknown_research
        research_signatures = sync_storage_bag_unknown_research(projected, before, session=session)
        from backend.core.fanxiu.storage_bag_unknown_research import project_storage_bag_research_policy
        projected = project_storage_bag_research_policy(projected, current_signatures=research_signatures)
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
    try:
        from backend.core.fanxiu.storage_bag_unknown_research import record_storage_bag_research_plan_evidence
        record_storage_bag_research_plan_evidence(before, plan.to_dict())
    except Exception as exc:
        # This is a diagnostic copy of the validated plan, never a retry gate:
        # losing its receipt cannot change game actions or replay prior effects.
        import logging
        logging.getLogger(__name__).warning('Storage research plan diagnostic unavailable: %s', type(exc).__name__)
        try:
            from backend.core.fanxiu.storage_bag_unknown_research import record_storage_bag_research_plan_diagnostic_failure
            record_storage_bag_research_plan_diagnostic_failure(exc)
        except Exception:
            logging.getLogger(__name__).warning('Storage research plan failure receipt also unavailable')
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
    """Inspect the live selected batch before any aggregate mutation.

    Item-level plans distinguish executable components, independent NPC routes,
    and deferred adapters; aggregate counts alone cannot prove their coverage.
    """

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    yield from reconcile_pending_disciple_letters(context, snapshot_reader)
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
            "返回",
            STORAGE_BAG_SCENE,
            # 已实测左下“返回”关闭数量弹窗；留预算等待底页稳定识别。
            timeout=30.0,
            label="储物袋_操作/整单重入：关闭遗留 #584 数量确认",
        )
    # 已消费的拜师函结果只收尾，不重放使用；新整单随后按当前库存规划。
    if int(scene or 0) in (942, 935, 936):
        yield from finish_existing_disciple_travel(context)
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
    storage_bag_balanced_profiles(payload,plan.action_queue,_cards_by_id)
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
        "planned_items": [entry.to_dict() for entry in plan.action_queue],
        "routed_items": [entry.to_dict() for entry in plan.routed],
        "deferred_items": [entry.to_dict() for entry in plan.deferred],
        "quick_operation_allowed": not _quick_operation_blockers(plan),
        "quick_operation_blockers": _blocker_records(plan),
        "runtime_fingerprint": plan.runtime_fingerprint,
    }


def parse_spirit_stone_research_request(payload: Mapping[str, Any]) -> StorageBagDirectUseRequest:
    """Require an exact stack and caller-declared cap; never select another item.

    The current adapter supports whole-stack use only. A partial quantity must
    fail its live Runtime equality check, rather than silently becoming all-use.
    """
    if payload.get("base_id") != SPIRIT_STONE_BASE_ID:
        raise StorageBagAutoClaimBlocked("灵石验收只允许base1001")
    quantity = payload.get("quantity")
    maximum = payload.get("maximum_quantity")
    instance = payload.get("instance_id")
    if (type(quantity) is not int or type(maximum) is not int
            or quantity <= 0 or maximum <= 0 or quantity > maximum
            or not isinstance(instance, str) or not instance.strip()):
        raise StorageBagAutoClaimBlocked("灵石验收需要明确实例、正整数数量及消费上限")
    return StorageBagDirectUseRequest(SPIRIT_STONE_BASE_ID, instance.strip(),
                                     SPIRIT_STONE_NAME, quantity)


def execute_storage_bag_spirit_stone_research_task(
    runner: Any, ctx: dict[str, Any], payload: dict[str, Any], stop_event: threading.Event,
    *, snapshot_reader: SnapshotReader = fanxiu_instrumentation_service.backpack_ui_snapshot,
    session_factory: SessionFactory = _default_session_factory,
) -> Generator[Any, Any, dict[str, Any]]:
    """Explicit single-stack acceptance under existing auto_claim authorization.

    This branch never runs the general selected queue or changes the production
    switch. A live equality check binds the declared entire stack; verified bag
    and wallet deltas are durably recorded before returning to world. Failure
    preserves the first scene for the owning AI and never retries consumption.
    Caller owns Scheduler admission, exclusive control and code loading.
    """
    request = parse_spirit_stone_research_request(payload)
    with session_factory() as session:
        selected = apply_storage_bag_item_settings(session,
            {"items": [{"base_id": SPIRIT_STONE_BASE_ID}]})["items"][0]
    if (selected.get("auto_claim") is not True
            or selected.get("analysis_status") != "classified"
            or selected.get("operation_template") != "direct_use"):
        raise StorageBagAutoClaimBlocked("灵石未在持久auto_claim直接使用授权范围内")
    replay_storage_bag_open_receipts(session_factory=session_factory)
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(WORLD_SCENE)
    verified_action_key = payload.get("verified_action_key")
    if verified_action_key is not None:
        # Explicit receipt acceptance reentry: never recreate a consumed stack
        # or touch another instance merely to test the outer Task terminal.
        documents = [row for row in list_storage_bag_open_receipts()
                     if row.get("receipt", {}).get("action_key") == verified_action_key]
        if len(documents) != 1 or documents[0].get("state") != "committed":
            raise StorageBagAutoClaimBlocked("灵石验收重入缺少唯一已落库原子凭证")
        receipt = documents[0]["receipt"]
        evidence = receipt.get("evidence") or {}
        if (receipt.get("base_id") != request.base_id
                or receipt.get("operation_template") != "direct_use"
                or receipt.get("opened_count") != request.quantity
                or evidence.get("instance_id") != request.instance_id
                or evidence.get("wallet_after", 0) - evidence.get("wallet_before", 0)
                != request.quantity):
            raise StorageBagAutoClaimBlocked("灵石验收重入凭证与显式单叠请求不一致")
        return {"message": "灵石已提交原子凭证验收重入，未重复使用，已返回世界",
                "outcome": "research_verified", "base_id": request.base_id,
                "instance_id": request.instance_id, "consumed_quantity": request.quantity,
                "wallet_delta": request.quantity, "action_key": verified_action_key,
                "receipt_reentry": True, "new_consumed_quantity": 0}
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene([STORAGE_BAG_SCENE], wait=10.0,
                                  label="灵石单叠验收：进入储物袋")
    adapter = StorageBagSpiritStoneGuiAdapter(context=context,
        snapshot_reader=snapshot_reader, wallet_snapshot_reader=_wallet_snapshot_reader)
    result = yield from adapter.execute(request)
    receipt = result.receipt()
    persist_storage_bag_open_receipt(receipt, session_factory=session_factory)
    yield from context.go_scene(WORLD_SCENE)
    return {"message": "灵石单叠验收已核对双差值并落原子凭证，返回世界",
            "outcome": "research_verified", "base_id": request.base_id,
            "instance_id": request.instance_id, "consumed_quantity": result.consumed_quantity,
            "wallet_delta": result.wallet_delta, "action_key": receipt["action_key"]}


def execute_storage_bag_vip_research_task(
    runner, ctx, payload, stop_event, *,
    snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
    session_factory=_default_session_factory,
):
    """Accept one explicitly selected VIP stack; production remains closed."""
    from .storage_bag_vip import StorageBagVipGuiAdapter, read_vip_direct_use_progress
    from backend.core.fanxiu.catalog.item import get_fanxiu_item_card
    quantity, maximum, instance = (payload.get(key) for key in
                                    ("quantity", "maximum_quantity", "instance_id"))
    if (payload.get("base_id") != 1010 or type(quantity) is not int or quantity <= 0
            or type(maximum) is not int or quantity > maximum
            or not isinstance(instance, str) or not instance.strip()):
        raise StorageBagAutoClaimBlocked("VIP验收需要base1010、精确实例/数量/消费上限")
    with session_factory() as session:
        selected = apply_storage_bag_item_settings(session,
            {"items": [{"base_id": 1010}]})["items"][0]
    if (selected.get("auto_claim") is not True or selected.get("analysis_status") != "classified"
            or selected.get("operation_template") != "direct_use"):
        raise StorageBagAutoClaimBlocked("VIP不在持久勾选直接使用授权内")
    card = get_fanxiu_item_card(1010)["card"]
    if (str(card.get("effect_value")) != "1" or card.get("type") != 32
            or card.get("sub_type") != 23):
        raise StorageBagAutoClaimBlocked("VIP材料每个1经验的配置契约失配")
    replay_storage_bag_open_receipts(session_factory=session_factory)
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(WORLD_SCENE)
    verified_action_key = payload.get("verified_action_key")
    if verified_action_key is not None:
        documents = [row for row in list_storage_bag_open_receipts()
                     if row.get("receipt", {}).get("action_key") == verified_action_key]
        if len(documents) != 1 or documents[0].get("state") != "committed":
            raise StorageBagAutoClaimBlocked("VIP重入缺少唯一已提交凭证")
        receipt = documents[0]["receipt"]
        evidence = receipt.get("evidence") or {}
        if (receipt.get("base_id") != 1010 or receipt.get("operation_template") != "direct_use"
                or receipt.get("opened_count") != quantity
                or evidence.get("instance_id") != instance.strip()
                or type(evidence.get("role_id")) is not int or evidence["role_id"] <= 0
                or evidence.get("cumulative_vip_exp_after", 0)
                - evidence.get("cumulative_vip_exp_before", 0) != quantity):
            raise StorageBagAutoClaimBlocked("VIP重入凭证与显式单叠请求不一致")
        return {"message": "VIP已提交凭证重入，未重复消费并返回世界",
                "outcome": "research_verified", "base_id": 1010,
                "instance_id": instance.strip(), "consumed_quantity": quantity,
                "progress_delta": quantity, "action_key": verified_action_key,
                "receipt_reentry": True, "new_consumed_quantity": 0}
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene([STORAGE_BAG_SCENE], wait=10.0, label="VIP单叠验收：进入储物袋")
    request = StorageBagDirectUseRequest(1010, instance.strip(), "VIP经验", quantity)
    adapter = StorageBagVipGuiAdapter(context=context, snapshot_reader=snapshot_reader,
                                     wallet_snapshot_reader=read_vip_direct_use_progress)
    result = yield from adapter.execute(request)
    receipt = result.receipt()
    persist_storage_bag_open_receipt(receipt, session_factory=session_factory)
    yield from context.go_scene(WORLD_SCENE)
    return {"message": "VIP单叠背包与累计经验双差值已验收落库并返回世界",
            "outcome": "research_verified", "base_id": 1010,
            "instance_id": request.instance_id, "consumed_quantity": result.consumed_quantity,
            "progress_delta": result.wallet_delta, "action_key": receipt["action_key"]}


def execute_storage_bag_ring_research_task(runner, ctx, payload, stop_event, *,
        snapshot_reader=fanxiu_instrumentation_service.backpack_ui_snapshot,
        session_factory=_default_session_factory):
    """Measure one selected ring box through the existing box transaction."""
    from .storage_bag_ring import RING_BOX_IDS, ring_box_reward_targets, verify_ring_reward_delta
    from backend.core.fanxiu.instrumentation.backpack import read_equipment_gem_inventory_snapshot
    base, stock, instance = (payload.get(key) for key in ("base_id", "stock_quantity", "instance_id"))
    if (base not in RING_BOX_IDS or payload.get("quantity") != 1
            or type(payload.get("quantity")) is not int or payload.get("maximum_quantity") != 1
            or type(payload.get("maximum_quantity")) is not int
            or type(stock) is not int or stock < 1 or not isinstance(instance, str) or not instance.strip()):
        raise StorageBagAutoClaimBlocked("灵环研究只允许已授权两种箱子各显式测1箱")
    with session_factory() as session:
        selected = apply_storage_bag_item_settings(session, {"items": [{"base_id": base}]})["items"][0]
    if (selected.get("auto_claim") is not True or selected.get("analysis_status") != "classified"
            or selected.get("operation_template") != "random_box"):
        raise StorageBagAutoClaimBlocked("灵环箱不在持久勾选随机箱授权内")
    cards = dict(_default_catalog_reader())
    card = cards[str(base)]
    if not ring_box_reward_targets(card, cards):
        raise StorageBagAutoClaimBlocked("灵环箱缺少完整已配置候选")
    replay_storage_bag_open_receipts(session_factory=session_factory)
    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
    yield from context.go_scene(WORLD_SCENE)
    verified_action_key = payload.get("verified_action_key")
    if verified_action_key is not None:
        documents = [row for row in list_storage_bag_open_receipts()
                     if row.get("receipt", {}).get("action_key") == verified_action_key]
        if len(documents) != 1 or documents[0].get("state") != "committed":
            raise StorageBagAutoClaimBlocked("灵环重入缺少唯一已提交凭证")
        receipt = documents[0]["receipt"]
        evidence = receipt.get("evidence") or {}
        if (receipt.get("base_id") != base or receipt.get("operation_template") != "random_box"
                or receipt.get("opened_count") != 1 or evidence.get("instance_id") != instance.strip()
                or evidence.get("expected_quantity") != stock
                or not str(verified_action_key).startswith("storage-bag-random_box:")):
            raise StorageBagAutoClaimBlocked("灵环重入凭证与显式单箱请求不一致")
        rewards = verify_ring_reward_delta(evidence.get("ring_inventory_before") or {},
                    evidence.get("ring_inventory_after") or {},
                    targets=ring_box_reward_targets(card, cards), opened_count=1,
                    process_identity=(evidence.get("pid"), evidence.get("process_start_ticks")))
        if receipt.get("rewards") != rewards:
            raise StorageBagAutoClaimBlocked("灵环重入凭证奖励与独立库存差值不一致")
        return {"message": "灵环原子凭证重入成功，未重复开箱，已返回世界",
                "outcome": "research_verified", "base_id": base, "instance_id": instance.strip(),
                "consumed_quantity": 1, "ring_delta": 1, "action_key": verified_action_key,
                "receipt_reentry": True, "new_consumed_quantity": 0}
    yield from context.wait_click(WORLD_SCENE, "右侧菜单/储物袋", timeout=10.0)
    yield from context.wait_scene([STORAGE_BAG_SCENE], wait=10.0, label="灵环单箱验收：进入储物袋")
    adapter = StorageBagRandomBoxGuiAdapter(context=context, snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards, wallet_snapshot_reader=_wallet_snapshot_reader,
        ring_inventory_reader=lambda: read_equipment_gem_inventory_snapshot(allow_discovery=True),
        recorder=lambda execution: persist_box_execution_after_verified_open(execution,
                                                   session_factory=session_factory))
    result = yield from adapter.execute(StorageBagRandomBoxRequest(base, instance.strip(),
                                                                  card["name"], stock, open_quantity=1))
    yield from context.go_scene(WORLD_SCENE)
    return {"message": "灵环单箱背包与独立库存双差值已落库并返回世界",
            "outcome": "research_verified", "base_id": base, "instance_id": instance.strip(),
            "consumed_quantity": result.delta.opened_count,
            "ring_delta": sum(reward["quantity"] for reward in result.delta.rewards),
            "action_key": result.action_key}


def execute_storage_bag_auto_claim_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    max_actions: int | None = None,
    batch_admission_reader: Callable[[], Mapping[str, Any]] | None = None,
    navigation_admission_reader: Callable[[], Mapping[str, Any]] | None = None,
    snapshot_reader: SnapshotReader = fanxiu_instrumentation_service.backpack_ui_snapshot,
    catalog_reader: CatalogReader = _default_catalog_reader,
    session_factory: SessionFactory = _default_session_factory,
    spirit_stone_direct_use_enabled: bool = (
        SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED
    ),
) -> Generator[Any, Any, dict[str, Any]]:
    """执行当前持久选择；可按完整物品动作分批，为到期作业留出窗口。

    默认执行整单；max_actions 仅限制本次已验证队列的动作数，不切开单个
    物品的使用与核验。部分完成返回 partial 和剩余计划项数，安全返回世界。
    下一次重新读取真实库存并规划，不保存续跑游标。正式 Job 不传批量上限，
    但在完整物品边界检查到期准入，时间不足返回部分结果与 retry_at；
    只有整单完成才推进日周期，部分执行不能冒充整单验收。
    """

    if max_actions is not None and (type(max_actions) is not int or max_actions <= 0):
        raise ValueError("储物袋批量上限必须是正整数或 None")

    context = runner._behavior_tree_context(
        ctx, ctx.get("asset_tree_path"), stop_event=stop_event,
    )
    # Pending consumption is settled before admission or fresh inventory plans.
    # It records an uncertain business effect, never a GUI step to replay.
    yield from reconcile_pending_disciple_letters(context, snapshot_reader)
    if batch_admission_reader is not None:
        admission = dict(batch_admission_reader())
        if not admission.get('admitted'):
            # Opening and reading the whole bag also takes real time. Refuse
            # before navigation, then rebuild the plan from a later snapshot.
            return {'ok': True, 'outcome': 'partial', 'executed_count': 0,
                    'reason': '整单观察与首批动作让出到期作业',
                    'yield_for_due_job': admission}

    replay_storage_bag_open_receipts(session_factory=session_factory)
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
            "返回",
            STORAGE_BAG_SCENE,
            timeout=8.0,
            label="储物袋_操作/整单重入：关闭遗留 #584 数量确认",
        )
    # 已消费的拜师函结果只收尾，不重放使用；新整单随后按当前库存规划。
    if int(scene or 0) in (942, 935, 936):
        yield from finish_existing_disciple_travel(context)
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
    balanced_profiles = storage_bag_balanced_profiles(payload,plan.action_queue,cards_by_id)

    def recorder(execution) -> None:
        persist_box_execution_after_verified_open(
            execution,
            session_factory=session_factory,
        )

    def guarded_click_planner(context, snapshot, request, **kwargs):
        def check():
            if navigation_admission_reader is not None:
                admission = dict(navigation_admission_reader())
                if not admission.get('admitted'):
                    raise StorageBagNavigationYield(admission)
        check()
        plan = yield from plan_current_random_box_click(context, snapshot, request, **kwargs)
        report = getattr(context, 'report_progress', None)
        if callable(report):
            report(f'储物袋定位：base_id={request.base_id} instance_id={request.instance_id} '
                   f'status={plan.status} target_index={plan.runtime_index} '
                   f'viewport_start={plan.viewport_runtime_start}')
        check()
        return plan

    random_adapter = StorageBagRandomBoxGuiAdapter(
        click_planner=guarded_click_planner,
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=_wallet_snapshot_reader,
        ring_inventory_reader=lambda: read_equipment_gem_inventory_snapshot(allow_discovery=True),
    )
    fixed_adapter = StorageBagFixedBoxGuiAdapter(
        click_planner=guarded_click_planner,
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=recorder,
        wallet_snapshot_reader=_wallet_snapshot_reader,
    )
    choice_adapter = StorageBagChoiceBoxGuiAdapter(
        click_planner=guarded_click_planner,
        context=context,
        snapshot_reader=snapshot_reader,
        catalog_cards_by_id=cards_by_id,
        recorder=record_storage_bag_choice_execution,
    )
    spirit_stone_adapter = StorageBagSpiritStoneGuiAdapter(
        click_planner=guarded_click_planner,
        context=context,
        snapshot_reader=snapshot_reader,
        wallet_snapshot_reader=_wallet_snapshot_reader,
    )

    executions: list[dict[str, Any]] = []
    from .storage_bag_vip import StorageBagVipGuiAdapter, read_vip_direct_use_progress
    vip_adapter = StorageBagVipGuiAdapter(context=context,
        click_planner=guarded_click_planner,
        snapshot_reader=snapshot_reader, wallet_snapshot_reader=read_vip_direct_use_progress)
    letter_adapter = StorageBagDiscipleLetterGuiAdapter(
        detail_click_planner=guarded_click_planner,
        context=context, snapshot_reader=snapshot_reader, catalog_cards_by_id=cards_by_id,
    )
    letter_capacity_finished = False
    previous_box_adapter = None
    due_job_admission = None
    try:
        entries = plan.action_queue if max_actions is None else plan.action_queue[:max_actions]
        for entry in entries:
            if batch_admission_reader is not None:
                due_job_admission = dict(batch_admission_reader())
                if not due_job_admission.get('admitted'):
                    break
            if executions and entry.template == 'direct_use':
                # Boxes may add to this exact stack after the opening plan.
                # Recheck current selections and classification as well as
                # quantity; the adapter still performs its own fresh guard.
                _fresh_cards, fresh_plan = _build_validated_production_plan(
                    dict(snapshot_reader()), catalog_reader=catalog_reader,
                    session_factory=session_factory,
                    spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
                )
                entry = refresh_storage_bag_auto_claim_entry(entry, fresh_plan)
                if entry is None:
                    continue
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
            if is_disciple_letter_entry(entry):
                if letter_capacity_finished:
                    result = {'verified': True, 'outcome': 'shared_capacity_already_filled',
                              'used_count': 0, 'shared_capacity_finished': True}
                else:
                    result = yield from letter_adapter.execute(_random_request(entry))
                    letter_capacity_finished = result['shared_capacity_finished']
            elif entry.template == "open_random_box":
                result = yield from random_adapter.execute(_random_request(entry))
            elif entry.template == "open_fixed_box":
                result = yield from fixed_adapter.execute(_random_request(entry))
            elif entry.template == "choice_box":
                if entry.base_id in balanced_profiles:
                    result = yield from execute_balanced_storage_bag_choice(
                        choice_adapter,_choice_request(entry),choices=balanced_profiles[entry.base_id],
                        non_prayer_verified=True)
                    if result["remaining_boxes"]:
                        raise StorageBagAutoClaimBlocked("均衡自选可选额度不足，整叠未完成")
                    # 同一base_id多叠共享本attempt的候选额度；这是本次
                    # 函数内预算，不能持久为续跑游标或在下一attempt重放。
                    consumed = {}
                    for execution in result["executions"]:
                        reward = execution.delta.reward_base_id
                        consumed[reward] = consumed.get(reward,0)+execution.delta.opened_count
                    balanced_profiles[entry.base_id] = tuple(replace(c,
                        available_boxes=c.available_boxes-consumed.get(c.reward_id,0))
                        if c.available_boxes is not None else c for c in balanced_profiles[entry.base_id])
                else:
                    result = yield from choice_adapter.execute(_choice_request(entry))
            elif _is_enabled_vip_entry(entry):
                result = yield from vip_adapter.execute(_direct_use_request(entry))
                persist_storage_bag_open_receipt(result.receipt(), session_factory=session_factory)
            elif _is_enabled_spirit_stone_entry(
                entry,
                spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
            ):
                result = yield from spirit_stone_adapter.execute(
                    _direct_use_request(entry)
                )
                # Save verified consumption before proceeding. Projection failure
                # leaves a durable pending receipt; replay never clicks again.
                persist_storage_bag_open_receipt(
                    result.receipt(), session_factory=session_factory,
                )
            else:  # guarded by _validate_production_batch
                raise AssertionError(f"unreachable storage-bag template: {entry.template}")
            executions.append({
                "base_id": entry.base_id,
                "instance_id": entry.instance_id,
                "template": entry.template,
                "verified": result is not None,
                **({'result': result} if is_disciple_letter_entry(entry) else {}),
            })
        # The planning atlas describes the pre-action bag. Publish a complete
        # post-action observation while #525 is still open; never subtract
        # planned quantities or infer other reward inventory from clicks.
        after = dict(snapshot_reader())
        sync_storage_bag_atlas(
            after, cards_by_id,
            captured_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
        )
    except StorageBagNavigationYield as exc:
        # No item submission occurs in the locator. Preserve prior verified
        # actions, refresh the post-state and leave normally; no half-item replay.
        due_job_admission = exc.admission
        after = dict(snapshot_reader())
        sync_storage_bag_atlas(after, cards_by_id,
            captured_at=datetime.now().astimezone().isoformat(timespec='microseconds'))
    except Exception:
        # Scheduler stops the queue on failure and owns the incident hold.
        # Preserve the first failing page and pending-effect evidence under
        # engineering as well as AI control; a new attempt reconciles it.
        raise

    # Replan the complete post-state, rather than treating exhaustion of the
    # opening queue as exhaustion of current stock. This performs no GUI action
    # and carries no cursor or quantity subtraction into the next attempt.
    _current_cards, current_plan = _build_validated_production_plan(
        after, catalog_reader=catalog_reader, session_factory=session_factory,
        spirit_stone_direct_use_enabled=spirit_stone_direct_use_enabled,
    )
    remaining_current = remaining_storage_bag_current_items(
        current_plan, disciple_capacity_finished=letter_capacity_finished,
    )
    from .storage_bag_stock_evidence import project_storage_bag_new_stock_evidence
    try:
        stock_evidence = project_storage_bag_new_stock_evidence(
            before, after, [entry.to_dict() for entry in remaining_current],
        )
    except Exception as exc:
        # Diagnostic loss cannot turn already verified consumption into retry.
        stock_evidence = {'read_only': True, 'observation_verified': False,
                          'new_actionable_stock_verified': False,
                          'reason': 'stock_diagnostic_failed',
                          'error_type': type(exc).__name__}
    yield from context.wait_click(STORAGE_BAG_SCENE, "返回", timeout=8.0)
    yield from context.wait_scene(
        [WORLD_SCENE],
        wait=10.0,
        label="储物袋_操作/勾选物品：返回世界",
    )
    result = {
        "ok": True,
        "outcome": (
            "partial_safe" if any(entry.completion_required for entry in current_plan.deferred)
            else "partial" if remaining_current or len(executions) != len(plan.action_queue)
            else "complete"
        ),
        "unresolved_items": [entry.to_dict() for entry in current_plan.deferred if entry.completion_required],
        "remaining_current_items": [entry.to_dict() for entry in remaining_current],
        "disciple_capacity_finished": letter_capacity_finished,
        "scheduler_attempt_id": payload.get('__scheduler_attempt_id'),
        "planned_action_count": len(plan.action_queue),
        "remaining_planned_action_count": len(plan.action_queue) - len(executions),
        "opening_selected_base_count": plan.selected_base_count,
        "opening_routed_count": len(plan.routed),
        "opening_deferred_count": len(plan.deferred),
        "selected_base_count": current_plan.selected_base_count,
        "executed_count": len(executions),
        "routed_count": len(current_plan.routed),
        "deferred_count": len(current_plan.deferred),
        "routed_items": [entry.to_dict() for entry in current_plan.routed],
        "deferred_items": [entry.to_dict() for entry in current_plan.deferred],
        "quick_operation_blockers": _blocker_records(current_plan),
        "runtime_fingerprint": plan.runtime_fingerprint,
        "final_runtime_fingerprint": after.get("fingerprint"),
        "new_stock_observation": stock_evidence,
        "executions": executions,
        **({'yield_for_due_job': due_job_admission} if due_job_admission is not None
           and not due_job_admission.get('admitted') else {}),
    }
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    record_world_discovery('storage-bag-auto-claim:scope', result)
    return result


__all__ = [
    "SPIRIT_STONE_DIRECT_USE_PRODUCTION_ENABLED",
    "SUPPORTED_PRODUCTION_TEMPLATES",
    "execute_storage_bag_auto_claim_task",
    "preflight_storage_bag_auto_claim_task",
    "execute_storage_bag_spirit_stone_research_task",
    "parse_spirit_stone_research_request",
]
