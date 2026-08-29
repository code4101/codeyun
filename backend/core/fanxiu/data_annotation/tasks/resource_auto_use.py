from __future__ import annotations

"""Thin, fail-closed aggregate for broad resource auto-use operations."""

import threading
from collections.abc import Callable, Generator
from typing import Any

from backend.core.fanxiu.data_annotation.tasks.resource_auto_use_policy import (
    ResourceAutoUseDecision,
    plan_pet_quick_swallow,
    plan_talisman_quick_upgrade,
    verify_pet_quick_swallow_effect,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_operation import (
    execute_storage_bag_operation_task,
)
from backend.core.fanxiu.data_annotation.tasks.storage_bag_auto_claim_execution import (
    execute_storage_bag_auto_claim_task,
    preflight_storage_bag_auto_claim_task,
)
from backend.core.fanxiu.instrumentation.resource_auto_use import (
    read_pet_quick_swallow_runtime,
    read_talisman_quick_upgrade_runtime,
)


STANDARD_JOB_ID = "resource-auto-use"
TASK_TYPE = "resource_auto_use"

SnapshotReader = Callable[[], dict[str, Any]]
DomainAdapter = Callable[
    [Any, dict[str, Any], dict[str, Any], threading.Event, dict[str, Any]],
    Generator[Any, Any, dict[str, Any]],
]
StorageExecutor = Callable[
    [Any, dict[str, Any], dict[str, Any], threading.Event],
    Generator[Any, Any, dict[str, Any]],
]

PET_HOME_SCENE_ID = 483
PET_QUICK_SWALLOW_CONFIRM_SCENE_ID = 555
PET_QUICK_SWALLOW_RESULT_SCENE_ID = 556


def complete_pet_quick_swallow(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    before: dict[str, Any],
):
    """Execute one fully authorized native ordinary-pet batch."""

    runtime = runner._fanxiu_runtime(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import (
        open_world_menu_function,
    )

    yield from open_world_menu_function(
        runtime,
        4000,
        expected_scene_ids=(PET_HOME_SCENE_ID,),
        timeout_seconds=30,
    )
    yield from runtime.wait_click(PET_HOME_SCENE_ID, "快速吞噬")
    yield from runtime.wait_scene(
        PET_QUICK_SWALLOW_CONFIRM_SCENE_ID,
        timeout=15,
        label="资源_自动使用/灵兽：等待快速吞噬确认",
    )
    yield from runtime.wait_click(PET_QUICK_SWALLOW_CONFIRM_SCENE_ID, "确认")
    yield from runtime.wait_scene(
        PET_QUICK_SWALLOW_RESULT_SCENE_ID,
        timeout=30,
        label="资源_自动使用/灵兽：等待吞噬结果",
    )
    after = read_pet_quick_swallow_runtime()
    if not verify_pet_quick_swallow_effect(before, after):
        raise RuntimeError("资源_自动使用/灵兽：等级、库存或候选收敛未通过严格复验")
    yield from runtime.wait_click(PET_QUICK_SWALLOW_RESULT_SCENE_ID, "继续")
    yield from runtime.wait_scene(
        PET_HOME_SCENE_ID,
        timeout=20,
        label="资源_自动使用/灵兽：结果页返回灵兽主页",
    )
    yield from runtime.wait_click(PET_HOME_SCENE_ID, "返回")
    yield from runtime.wait_scene(34, timeout=20, label="资源_自动使用/灵兽：返回世界")
    return {"ok": True, "verified": True}


def _decision_record(
    domain: str,
    snapshot: dict[str, Any],
    decision: ResourceAutoUseDecision,
) -> dict[str, Any]:
    return {
        "domain": domain,
        "outcome": decision.action,
        "reason": decision.reason,
        "candidate_count": decision.candidate_count,
        "expected_units": decision.expected_units,
        "snapshot_state": snapshot.get("state"),
        "snapshot_source": snapshot.get("source"),
    }


def _run_snapshot_domain(
    *,
    domain: str,
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    reader: SnapshotReader,
    planner: Callable[[dict[str, Any]], ResourceAutoUseDecision],
    adapter: DomainAdapter | None,
):
    """Observe, decide, optionally act, and require a terminal re-observation."""

    before = reader()
    decision = planner(before)
    record = _decision_record(domain, before, decision)
    if decision.action == "complete":
        # A proven empty native candidate set is a true zero-UI completion.
        return record
    if decision.action == "fail":
        raise RuntimeError(f"资源_自动使用/{domain}：{decision.reason}")
    if adapter is None:
        raise RuntimeError(
            f"资源_自动使用/{domain}：候选需要执行，但正式资产/动作适配器尚未就绪；拒绝猜测点击"
        )

    action_result = yield from adapter(
        runner,
        ctx,
        payload,
        stop_event,
        before,
    )
    after = reader()
    after_decision = planner(after)
    if after_decision.action != "complete":
        raise RuntimeError(
            f"资源_自动使用/{domain}：动作后未取得完整终态：{after_decision.reason}"
        )
    return {
        **_decision_record(domain, after, after_decision),
        "action_result": action_result,
    }


def execute_resource_auto_use_task(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    talisman_reader: SnapshotReader = read_talisman_quick_upgrade_runtime,
    pet_reader: SnapshotReader = read_pet_quick_swallow_runtime,
    talisman_adapter: DomainAdapter | None = None,
    pet_adapter: DomainAdapter | None = complete_pet_quick_swallow,
    storage_auto_claim_preflight: StorageExecutor | None = None,
    storage_auto_claim_executor: StorageExecutor = execute_storage_bag_auto_claim_task,
):
    """Run storage preflight -> quick op -> fresh selected plan -> other domains.

    The selected-item preflight identifies unsupported present items before the
    mutating quick operation.  Those items remain deferred and block the broad
    ``Use=ON`` action, while exact adapters may still consume the independently
    validated safe subset.  The executor deliberately reads and validates again
    instead of reusing the preflight snapshot.  Other domains may skip UI only
    when their native read-only snapshot proves an empty candidate set.
    """

    domains: list[dict[str, Any]] = []
    preflight = storage_auto_claim_preflight or preflight_storage_bag_auto_claim_task
    preflight_result = yield from preflight(
        runner,
        ctx,
        payload,
        stop_event,
    )
    if not isinstance(preflight_result, dict) or not preflight_result.get("ok"):
        raise RuntimeError("资源_自动使用/储物袋：勾选物品全局预检未返回就绪终态")
    if preflight_result.get("quick_operation_allowed") is False:
        storage_result = {
            "ok": True,
            "outcome": "skipped_fail_closed",
            "reason": "存在尚无正式生产适配器的已勾选可使用物品，拒绝宽泛 Use=ON 快捷操作",
            "blockers": list(preflight_result.get("quick_operation_blockers") or []),
        }
    else:
        storage_result = yield from execute_storage_bag_operation_task(
            runner,
            ctx,
            payload,
            stop_event,
        )
        if not isinstance(storage_result, dict) or not storage_result.get("ok"):
            raise RuntimeError("资源_自动使用/储物袋：既有完整流程未返回成功终态")
    selected_result = yield from storage_auto_claim_executor(
        runner,
        ctx,
        payload,
        stop_event,
    )
    if not isinstance(selected_result, dict) or not selected_result.get("ok"):
        raise RuntimeError("资源_自动使用/储物袋：勾选物品流程未返回成功终态")
    storage_outcome = (
        "partial_safe"
        if storage_result.get("outcome") == "skipped_fail_closed"
        else "complete"
    )
    domains.append({
        "domain": "储物袋",
        "outcome": storage_outcome,
        "quick_operation": storage_result,
        "selected_items": selected_result,
    })

    domains.append(
        (
            yield from _run_snapshot_domain(
                domain="法宝",
                runner=runner,
                ctx=ctx,
                payload=payload,
                stop_event=stop_event,
                reader=talisman_reader,
                planner=plan_talisman_quick_upgrade,
                adapter=talisman_adapter,
            )
        )
    )
    domains.append(
        (
            yield from _run_snapshot_domain(
                domain="灵兽",
                runner=runner,
                ctx=ctx,
                payload=payload,
                stop_event=stop_event,
                reader=pet_reader,
                planner=plan_pet_quick_swallow,
                adapter=pet_adapter,
            )
        )
    )
    outcome = (
        "partial_safe"
        if any(domain.get("outcome") == "partial_safe" for domain in domains)
        else "complete"
    )
    return {"ok": True, "outcome": outcome, "domains": domains}


__all__ = [
    "STANDARD_JOB_ID",
    "TASK_TYPE",
    "execute_resource_auto_use_task",
]
