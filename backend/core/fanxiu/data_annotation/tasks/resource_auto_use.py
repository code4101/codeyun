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
    execute_storage_bag_quick_operation_task,
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

    context = runner._behavior_tree_context(
        ctx,
        ctx.get("asset_tree_path"),
        stop_event=stop_event,
    )
    from backend.core.fanxiu.data_annotation.tasks.world_menu_navigation import (
        open_world_menu_function,
    )

    yield from open_world_menu_function(
        context,
        4000,
        expected_scene_ids=(PET_HOME_SCENE_ID,),
        timeout_seconds=30,
    )
    result = yield from complete_pet_quick_swallow_on_current_page(context)
    yield from context.wait_click(PET_HOME_SCENE_ID, "返回")
    yield from context.wait_scene([34], wait=20, label="资源_每日处理/灵兽：返回世界")
    return result


def complete_pet_quick_swallow_on_current_page(context: Any):
    """复用原生快速吞噬流程；完成后留在灵兽主页供后续步骤使用。"""
    yield from context.wait_scene([PET_HOME_SCENE_ID], wait=15)
    before = read_pet_quick_swallow_runtime()
    decision = plan_pet_quick_swallow(before)
    if decision.action == "complete":
        return {"ok": True, "verified": True, "status": "nothing_to_upgrade"}
    if decision.action != "execute":
        raise RuntimeError(decision.reason)
    yield from context.wait_click(PET_HOME_SCENE_ID, "快速吞噬")
    yield from context.wait_scene(
        [PET_QUICK_SWALLOW_CONFIRM_SCENE_ID],
        wait=15,
        label="资源_每日处理/灵兽：等待快速吞噬确认",
    )
    yield from context.wait_click(PET_QUICK_SWALLOW_CONFIRM_SCENE_ID, "确认")
    yield from context.wait_scene(
        [PET_QUICK_SWALLOW_RESULT_SCENE_ID],
        wait=30,
        label="资源_每日处理/灵兽：等待吞噬结果",
    )
    after = read_pet_quick_swallow_runtime()
    if not verify_pet_quick_swallow_effect(before, after):
        raise RuntimeError("资源_每日处理/灵兽：等级、库存或候选收敛未通过严格复验")
    yield from context.wait_click(PET_QUICK_SWALLOW_RESULT_SCENE_ID, "继续")
    yield from context.wait_scene(
        [PET_HOME_SCENE_ID],
        wait=20,
        label="资源_每日处理/灵兽：结果页返回灵兽主页",
    )
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
        raise RuntimeError(f"资源_每日处理/{domain}：{decision.reason}")
    if adapter is None:
        raise RuntimeError(
            f"资源_每日处理/{domain}：候选需要执行，但正式资产/动作适配器尚未就绪；拒绝猜测点击"
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
            f"资源_每日处理/{domain}：动作后未取得完整终态：{after_decision.reason}"
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
    stage_executor: Callable | None = None,
):
    """每日快捷使用；逐项定位、滚动和开箱仅由独立储物袋任务执行。"""

    def storage_action():
        result = yield from execute_storage_bag_quick_operation_task(
            runner, ctx, payload, stop_event,
        )
        if not isinstance(result, dict) or not result.get("ok") or result.get("outcome") != "complete":
            raise RuntimeError("资源_每日处理/储物袋快捷操作：未取得成功终态")
        return {"domain": "储物袋快捷操作", "outcome": "complete", "quick_operation": result}

    def run_stage(stage_id, operation):
        if stage_executor is not None:
            return (yield from stage_executor(stage_id, operation))
        return (yield from operation())

    domains: list[dict[str, Any]] = []
    domains.append((yield from run_stage("storage-quick-operation", storage_action)))
    for stage_id, domain, reader, planner, adapter in (
        ("talisman-upgrade", "法宝", talisman_reader, plan_talisman_quick_upgrade, talisman_adapter),
        ("pet-swallow", "灵兽", pet_reader, plan_pet_quick_swallow, pet_adapter),
    ):
        def operation(domain=domain, reader=reader, planner=planner, adapter=adapter):
            return (yield from _run_snapshot_domain(
                domain=domain, runner=runner, ctx=ctx, payload=payload,
                stop_event=stop_event, reader=reader, planner=planner, adapter=adapter,
            ))
        domains.append((yield from run_stage(stage_id, operation)))
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
