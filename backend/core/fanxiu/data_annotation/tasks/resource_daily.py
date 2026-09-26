"""资源每日处理：准备资源 → 领取 → 兑换 → 使用 → 培养。

本入口只组织业务阶段并提交下一次运行时间。组件周期、版本和完成凭证
由 ResourceDailyExecution 适配；每个业务组件负责自身幂等与真实终态。
"""
from __future__ import annotations

from backend.core.fanxiu.data_annotation.effective_time import job_now
from .aggregate_progress import AggregateJobProgress
from .resource_daily_components import (
    cultivate_daily_skills,
    exchange_daily_resources,
    prepare_daily_resources,
    use_daily_resources,
)
from .resource_daily_contract import (
    RESOURCE_DAILY_STAGES,
    RESOURCE_DAILY_TASK_ID,
    next_resource_daily_time,
)
from .resource_daily_execution import ResourceDailyExecution


def execute_resource_daily_task(runner, ctx, payload, stop_event):
    """Run one business occurrence; stop at the first unfinished component.

    A new attempt starts the same composition, reusing only completed receipts.
    The frozen occurrence prevents a run crossing midnight from mixing ledgers;
    the next trigger uses the actual completion time, as before this extraction.
    """
    task_id = str(payload.get("__scheduler_task_id") or RESOURCE_DAILY_TASK_ID)
    progress = AggregateJobProgress(
        task_id, str(payload.get("__scheduler_attempt_id") or ""),
        log=lambda message: runner._log("skip", f"资源_每日处理/{message}"),
    )
    run = ResourceDailyExecution(runner, ctx, payload, stop_event, progress, job_now())

    yield from prepare_daily_resources(run)
    yield from run.internalized(RESOURCE_DAILY_STAGES)
    from . import trial_manual
    yield from run.component(trial_manual, trial_manual.claim_trial_manual, '领取试炼手册')
    if run.moment.weekday() == 0:
        from . import growth_fund
        yield from run.component(growth_fund, growth_fund.claim_growth_fund, '成长基金领取',
                                 cycle=f'week:{run.moment.date().isoformat()}', with_moment=True)
    yield from exchange_daily_resources(run)
    yield from use_daily_resources(run)
    yield from cultivate_daily_skills(run)

    next_time = next_resource_daily_time(job_now())
    runner._persist_scheduler_task_next_time(task_id, next_time)
    return {"result": "success", "outcome": "complete", "domains": run.domains,
            "message": f"资源_每日处理：各组件已完成，下次 {next_time}"}
