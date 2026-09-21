"""每天领取资源并使用已有资源；每个业务组件独立保存完成事实。"""
from __future__ import annotations

from inspect import isgenerator

from backend.core.fanxiu.data_annotation.effective_time import job_now
from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
from backend.core.fanxiu.data_annotation.tasks.aggregate_progress import AggregateJobProgress
from backend.core.fanxiu.data_annotation.tasks.resource_daily_contract import (
    RESOURCE_DAILY_STAGES, RESOURCE_DAILY_TASK_ID, next_resource_daily_time,
    resource_daily_cycle_key,
)


def execute_resource_daily_task(runner, ctx, payload, stop_event):
    """One Scheduler owner, independent daily/weekly component receipts.

    A failed component stops the run; completed components remain durable. New
    attempts build new generators from current game facts. Child tasks cannot
    change the parent's next_time, and retired jobs are never recreated.
    """
    task_id = str(payload.get("__scheduler_task_id") or RESOURCE_DAILY_TASK_ID)
    progress = AggregateJobProgress(
        task_id, str(payload.get("__scheduler_attempt_id") or ""),
        log=lambda message: runner._log("skip", f"资源_每日处理/{message}"),
    )
    # Freeze one business occurrence. A long run across midnight cannot mix two
    # daily ledgers; tomorrow's run gets its own date and re-observes resources.
    moment = job_now()
    daily_cycle = moment.date().isoformat()
    domains = []
    internalized = payload.get("internalized_jobs") or {}
    for stage in RESOURCE_DAILY_STAGES:
        definition = get_fanxiu_data_annotation_task_cell_definition(stage.task_type)
        if definition is None:
            raise RuntimeError(f"资源_每日处理：缺少内部任务 {stage.task_type}")
        child_payload = dict((internalized.get(stage.task_id) or {}).get("payload") or {})
        child_payload.update({"schedule": False})
        for key in tuple(child_payload):
            if key.startswith("__scheduler_"):
                child_payload.pop(key)

        def execute_child(definition=definition, child_payload=child_payload, stage=stage):
            result = definition.handler(runner, ctx, child_payload, stop_event)
            if isgenerator(result):
                result = yield from result
            if result == "success":
                return {"result": "success", "component": stage.label}
            if not isinstance(result, dict) or result.get("result") != "success":
                raise RuntimeError(f"{stage.label} 未取得业务完成终态：{result!r}")
            # Runtime pointer/debug dumps are not business completion evidence.
            return {key: value for key, value in result.items()
                    if key not in {"backpack_debug", "backpack_debug_after"}}

        result = yield from progress.run(
            stage.task_id, resource_daily_cycle_key(stage, moment), execute_child,
        )
        domains.append({"domain": stage.label, "result": result})

    from backend.core.fanxiu.data_annotation.tasks.resource_auto_use import execute_resource_auto_use_task

    def run_resource_stage(stage_id, operation):
        return (yield from progress.run(stage_id, daily_cycle, operation))

    resources = yield from execute_resource_auto_use_task(
        runner, ctx, {**payload, "schedule": False}, stop_event,
        stage_executor=run_resource_stage,
    )
    domains.extend(resources["domains"])
    from backend.core.fanxiu.data_annotation.tasks.xianfu_science import (
        STAGE_ID, STAGE_VERSION, execute_xianfu_science_task,
    )
    science = yield from progress.run(
        STAGE_ID, daily_cycle,
        lambda: execute_xianfu_science_task(runner, ctx, payload, stop_event),
        version=STAGE_VERSION,
    )
    domains.append({"domain": "仙府玄机阁", "result": science})
    # Pending research (炼神/角色天赋/论道天赋树) is not represented by a
    # success-producing placeholder. Its production adapter is added only after
    # real GUI/Runtime acceptance, sharing the same component receipt contract.
    next_time = next_resource_daily_time(job_now())
    runner._persist_scheduler_task_next_time(task_id, next_time)
    return {"result": "success", "outcome": "complete", "domains": domains,
            "message": f"资源_每日处理：各组件已完成，下次 {next_time}"}
