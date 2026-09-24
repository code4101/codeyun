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
PET_PRAYER_SCENE_ID = 798
PET_PRAYER_RESULT_SCENE_ID = 799
# 真实观测（2026-09-21）：一次「快速祈灵」即消化当前全部可用祈灵材料，第二次点击不再出结果页。
# 仍保留有限循环以容忍分批结算，上限用于防止界面异常时空转。
PET_PRAYER_BATCH_LIMIT = 6
PET_PRAYER_RESULT_WAIT_SECONDS = 8.0


def complete_pet_quick_swallow(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    before: dict[str, Any],
):
    """灵兽每日闭环：快速吞噬 + 快速祈灵，一次进入灵兽主页内完成。

    吞噬仍由 Runtime 快照授权：快照证明无可升阶候选时不点吞噬界面。
    祈灵尚无等价 Runtime 投影，因此按正式页面动作执行，无可用材料时保持幂等终态。
    """

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
    swallow = yield from complete_pet_quick_swallow_on_current_page(context)
    prayer = yield from complete_pet_quick_prayer_on_current_page(context)
    yield from context.wait_click(PET_HOME_SCENE_ID, "返回")
    yield from context.wait_scene([34], wait=20, label="资源_每日处理/灵兽：返回世界")
    return {"swallow": swallow, "prayer": prayer}


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


def complete_pet_quick_prayer_on_current_page(context: Any):
    """在灵兽主页执行祈灵：进入祈灵页 → 快速祈灵 → 结果页继续 → 退回灵兽主页。

    无可用祈灵材料时「快速祈灵」不产生结果页，这是幂等终态而不是失败。
    本函数只负责动作链与页面终态，材料消耗量由游戏结算页自身呈现。
    """

    yield from context.wait_scene([PET_HOME_SCENE_ID], wait=15)
    yield from context.wait_click(PET_HOME_SCENE_ID, "祈灵")
    yield from context.wait_scene(
        [PET_PRAYER_SCENE_ID],
        wait=15,
        label="资源_每日处理/灵兽：等待祈灵页",
    )
    batches = 0
    while batches < PET_PRAYER_BATCH_LIMIT:
        yield from context.wait_click(PET_PRAYER_SCENE_ID, "快速祈灵")
        landed = yield from context.wait_scene(
            [PET_PRAYER_RESULT_SCENE_ID],
            wait=PET_PRAYER_RESULT_WAIT_SECONDS,
            required=False,
        )
        # 分层识别会返回候选之外的已知场景：没有可用材料时点击后仍停在祈灵页，
        # 此时 landing 会是 #798 而不是结果页。只有真正识别为结果页才算命中。
        if landed is None or int(landed.scene_id) != PET_PRAYER_RESULT_SCENE_ID:
            break
        batches += 1
        yield from context.wait_click(PET_PRAYER_RESULT_SCENE_ID, "继续")
        yield from context.wait_scene(
            [PET_PRAYER_SCENE_ID],
            wait=20,
            label="资源_每日处理/灵兽：祈灵结果返回祈灵页",
        )
    yield from context.wait_click(PET_PRAYER_SCENE_ID, "返回")
    yield from context.wait_scene(
        [PET_HOME_SCENE_ID],
        wait=20,
        label="资源_每日处理/灵兽：祈灵页返回灵兽主页",
    )
    return {"ok": True, "batches": batches, "consumed_materials": batches > 0}


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
    visit_when_complete: bool = False,
    before_snapshot: dict[str, Any] | None = None,
):
    """Observe, decide, optionally act, and require a terminal re-observation.

    ``visit_when_complete`` 用于「Runtime 已证明本域没有候选，但同一页面还承载无法用
    Runtime 证明的其它动作」的场景：仍进入页面执行那些动作，再按本域快照复验终态。
    """

    before = before_snapshot if before_snapshot is not None else reader()
    decision = planner(before)
    record = _decision_record(domain, before, decision)
    if decision.action == "complete" and not visit_when_complete:
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
    pending_adapters: list[str] = []
    domains.append((yield from run_stage("storage-quick-operation", storage_action)))
    for stage_id, domain, reader, planner, adapter, visit_when_complete in (
        ("talisman-upgrade", "法宝", talisman_reader, plan_talisman_quick_upgrade, talisman_adapter, False),
        # 灵兽：吞噬可由 Runtime 证明「无可升阶」而零界面完成，但祈灵尚无等价投影，
        # 因此即使吞噬候选为空也要进入灵兽主页，消费已积累的祈灵材料。
        ("pet-swallow", "灵兽", pet_reader, plan_pet_quick_swallow, pet_adapter, True),
    ):
        observed_snapshot = None
        if adapter is None:
            # An unimplemented domain must keep the aggregate Job incomplete,
            # but it need not starve later, independently checkpointed domains.
            # Do not commit a success receipt for this stage: the next attempt
            # must re-read its live candidates after an adapter is installed.
            observed_snapshot = reader()
            decision = planner(observed_snapshot)
            if decision.action == "execute":
                pending_adapters.append(domain)
                domains.append({**_decision_record(domain, observed_snapshot, decision),
                                "outcome": "pending_adapter"})
                continue
        def operation(domain=domain, reader=reader, planner=planner, adapter=adapter,
                      visit=visit_when_complete, snapshot=observed_snapshot):
            return (yield from _run_snapshot_domain(
                domain=domain, runner=runner, ctx=ctx, payload=payload,
                stop_event=stop_event, reader=reader, planner=planner, adapter=adapter,
                visit_when_complete=visit, before_snapshot=snapshot,
            ))
        domains.append((yield from run_stage(stage_id, operation)))
    if pending_adapters:
        raise RuntimeError(
            "资源_每日处理：" + "、".join(pending_adapters)
            + "有待执行候选，但正式资产/动作适配器尚未就绪；已继续其它已实现子项，作业保持未完成"
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
