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
    from backend.core.fanxiu.data_annotation.tasks.friend_notice import (
        STAGE_ID as FRIEND_NOTICE_STAGE_ID, dismiss_friend_notice,
    )
    friend_notice = yield from progress.run(
        FRIEND_NOTICE_STAGE_ID, daily_cycle,
        lambda: dismiss_friend_notice(runner._behavior_tree_context(ctx, stop_event=stop_event)),
    )
    domains.append({"domain": "好友提示", "result": friend_notice})
    from backend.core.fanxiu.data_annotation.tasks.reward_recovery import (
        STAGE_ID as RECOVERY_STAGE_ID, STAGE_VERSION as RECOVERY_STAGE_VERSION,
        recover_rewards,
    )
    recovery = yield from progress.run(
        RECOVERY_STAGE_ID, daily_cycle,
        lambda: recover_rewards(runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=RECOVERY_STAGE_VERSION,
    )
    domains.append({"domain": "找回", "result": recovery})
    from backend.core.fanxiu.data_annotation.tasks.divine_artifact_upgrade import (
        STAGE_ID as ARTIFACT_STAGE_ID, STAGE_VERSION as ARTIFACT_STAGE_VERSION,
        upgrade_divine_artifacts,
    )
    artifacts = yield from progress.run(
        ARTIFACT_STAGE_ID, daily_cycle,
        lambda: upgrade_divine_artifacts(runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=ARTIFACT_STAGE_VERSION,
    )
    domains.append({"domain": "神器升阶", "result": artifacts})
    from backend.core.fanxiu.data_annotation.tasks.spirit_artifact_prompt_update import (
        STAGE_ID as SPIRIT_ARTIFACT_STAGE_ID,
        STAGE_VERSION as SPIRIT_ARTIFACT_STAGE_VERSION,
        update_spirit_artifact_prompts,
    )
    spirit_artifacts = yield from progress.run(
        SPIRIT_ARTIFACT_STAGE_ID, daily_cycle,
        lambda: update_spirit_artifact_prompts(
            runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=SPIRIT_ARTIFACT_STAGE_VERSION,
    )
    domains.append({"domain": "洗灵更新", "result": spirit_artifacts})
    from backend.core.fanxiu.data_annotation.tasks.sword_spirit_update import (
        STAGE_ID as SWORD_SPIRIT_STAGE_ID,
        STAGE_VERSION as SWORD_SPIRIT_STAGE_VERSION,
        update_sword_spirit,
    )
    sword_spirit = yield from progress.run(
        SWORD_SPIRIT_STAGE_ID, daily_cycle,
        lambda: update_sword_spirit(runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=SWORD_SPIRIT_STAGE_VERSION,
    )
    domains.append({"domain": "剑灵更新", "result": sword_spirit})
    internalized = payload.get("internalized_jobs") or {}
    for stage in RESOURCE_DAILY_STAGES:
        if stage.monday_only and moment.weekday() != 0:
            continue
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

    from backend.core.fanxiu.data_annotation.tasks.dongtian_exchange import (
        STAGE_ID as DONGTIAN_STAGE_ID, STAGE_VERSION as DONGTIAN_STAGE_VERSION,
        dongtian_purchase_cycle, purchase_dongtian_resources,
    )
    dongtian_cycle = dongtian_purchase_cycle(moment)
    if dongtian_cycle is not None:
        dongtian = yield from progress.run(
            DONGTIAN_STAGE_ID, dongtian_cycle,
            lambda: purchase_dongtian_resources(
                runner._behavior_tree_context(ctx, stop_event=stop_event)),
            version=DONGTIAN_STAGE_VERSION,
        )
        domains.append({"domain": "洞天购买", "result": dongtian})

    from backend.core.fanxiu.data_annotation.tasks.resource_auto_use import execute_resource_auto_use_task

    def run_resource_stage(stage_id, operation):
        return (yield from progress.run(stage_id, daily_cycle, operation))

    resources = yield from execute_resource_auto_use_task(
        runner, ctx, {**payload, "schedule": False}, stop_event,
        stage_executor=run_resource_stage,
    )
    domains.extend(resources["domains"])
    from backend.core.fanxiu.data_annotation.tasks.gongfa_cultivation import (
        STAGE_ID as GONGFA_STAGE_ID, STAGE_VERSION as GONGFA_STAGE_VERSION,
        upgrade_gongfa_book,
    )
    gongfa = yield from progress.run(
        GONGFA_STAGE_ID, daily_cycle,
        lambda: upgrade_gongfa_book(runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=GONGFA_STAGE_VERSION,
    )
    domains.append({"domain": "升级功法书", "result": gongfa})
    from backend.core.fanxiu.data_annotation.tasks.lianshen_update import (
        STAGE_ID as LIANSHEN_STAGE_ID, STAGE_VERSION as LIANSHEN_STAGE_VERSION,
        update_lianshen,
    )
    lianshen = yield from progress.run(
        LIANSHEN_STAGE_ID, daily_cycle,
        lambda: update_lianshen(runner._behavior_tree_context(ctx, stop_event=stop_event)),
        version=LIANSHEN_STAGE_VERSION,
    )
    domains.append({"domain": "炼神更新", "result": lianshen})
    from backend.core.fanxiu.data_annotation.tasks.xianfu_science import (
        STAGE_ID, STAGE_VERSION, execute_xianfu_science_task,
    )
    science = yield from progress.run(
        STAGE_ID, daily_cycle,
        lambda: execute_xianfu_science_task(runner, ctx, payload, stop_event),
        version=STAGE_VERSION,
    )
    domains.append({"domain": "仙府玄机阁", "result": science})
    # Pending research (角色天赋/论道天赋树) is not represented by a
    # success-producing placeholder. Its production adapter is added only after
    # real GUI/Runtime acceptance, sharing the same component receipt contract.
    next_time = next_resource_daily_time(job_now())
    runner._persist_scheduler_task_next_time(task_id, next_time)
    return {"result": "success", "outcome": "complete", "domains": domains,
            "message": f"资源_每日处理：各组件已完成，下次 {next_time}"}
