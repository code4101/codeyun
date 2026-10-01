from __future__ import annotations

"""The two ranking-family Scheduler owners and their internal adapters."""

from datetime import datetime, time, timedelta
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import (
    BEAST_ABYSS_AUTO_CLEAR_KIND,
    BEAST_ABYSS_FORMAL_KIND,
    BEAST_ABYSS_INITIALIZATION_KIND,
    BEAST_ABYSS_MANUAL_CLEAR_KIND,
    BEAST_ABYSS_REGISTRATION_KIND,
    DAILY_RECONCILE_KIND,
    DANDAO_REWARDS_KIND,
    DANDAO_RESOURCE_USE_KIND,
    DANDAO_TAKE_MEDICINE_KIND,
    EXCHANGE_TAIL_KIND,
    MAGIC_INITIALIZATION_KIND,
    MAGIC_ACTIVE_KIND,
    MAGIC_FORMAL_KIND,
    MAGIC_MAIL_KIND,
    RANKING_CAPABILITY_STATUS,
    PRODUCTION_GAMEPLAY_EXCHANGE_TAIL_ACTIVITY_TYPES,
    RANKING_LIFECYCLE_TASK_ID,
    RESOURCE_FREE_GIFT_KIND,
    LINGZHUANG_STRENGTHENING_KIND,
    RESOURCE_RANKING_TASK_ID,
    SHENGXIAN_PEAK_FINAL_KIND,
    TIANDI_YIJU_ACTIVE_KIND,
    XUTIAN_ACTIVE_KIND,
    XUTIAN_OPEN_COLLECTION_KIND,
    XIANMENG_ACTIVE_KIND,
    YUNMENG_ACTIVE_KIND,
    YUNMENG_CHALLENGE_EVENING_KIND,
    YUNMENG_CHALLENGE_KIND,
    YUANDING_GIFT_KIND,
    YUANDING_RESOURCE_UNIT_KIND_SET,
    RankingFamily,
    discover_ranking_occurrences,
    due_ranking_checkpoints,
    next_ranking_lifecycle_time,
)
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys,
    ensure_ranking_lifecycle_checkpoint_table,
    ranking_checkpoint_retry_times,
    list_ranking_checkpoint_rows,
    record_ranking_checkpoint_result,
)
from backend.core.fanxiu.activity.ranking_reconcile import reconcile_ranking_occurrence, sync_ranking_schedule
from backend.core.fanxiu.data_annotation.effective_time import job_now


CHECKPOINT_RETRY_DELAY = timedelta(minutes=10)

# This is an execution-capability registry, not a declaration that the other
# activities lack an exchange tail.  The lifecycle planner derives that
# business fact from ExchangeActivitySpec.page.has_shop and Runtime's
# endTime/closePanelTime window.  Add an activity here only after its purchase
# flow has passed live, idempotent acceptance.
PRODUCTION_EXCHANGE_TAIL_EXECUTOR_ACTIVITY_TYPES = (
    PRODUCTION_GAMEPLAY_EXCHANGE_TAIL_ACTIVITY_TYPES
)


def exchange_tail_executor_is_production(activity_type: str) -> bool:
    return str(activity_type) in PRODUCTION_EXCHANGE_TAIL_EXECUTOR_ACTIVITY_TYPES


def _default_retry_policy(
    *,
    status: str,
    checkpoint,
    occurrence,
    now: datetime,
) -> tuple[str, datetime | None]:
    """Schedule business waiting; attempt counts never prove unavailability."""

    if status not in {"blocked", "pending"}:
        return status, None
    if checkpoint.checkpoint_kind in {
        DAILY_RECONCILE_KIND,
        MAGIC_INITIALIZATION_KIND,
    } and now < occurrence.start_at:
        return status, occurrence.start_at
    return status, now + CHECKPOINT_RETRY_DELAY


def _execute_magic_active_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from .magic_invasion_reward_target import execute_magic_invasion_evening_initialization
    return (yield from execute_magic_invasion_evening_initialization(
        runner, ctx, payload, stop_event, occurrence=occurrence
    ))


def _execute_magic_mail_checkpoint(runner, ctx, payload, stop_event, *, checkpoint):
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_mail import (
        execute_magic_invasion_mail_checkpoint,
    )

    return (yield from execute_magic_invasion_mail_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        business_day=datetime.fromisoformat(checkpoint.business_date).date(),
    ))


def _execute_xutian_active_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from backend.core.fanxiu.data_annotation.tasks.xutian_active import (
        execute_xutian_active_checkpoint,
    )
    return (yield from execute_xutian_active_checkpoint(
        runner, ctx, payload, stop_event, occurrence=occurrence
    ))


def _execute_xutian_open_collection_checkpoint(
    runner, ctx, stop_event, *, occurrence, captured_at, required_fact_watermark,
):
    from backend.core.fanxiu.data_annotation.tasks.xutian_open_collection import (
        execute_xutian_open_collection_checkpoint,
    )

    return (yield from execute_xutian_open_collection_checkpoint(
        runner,
        ctx,
        stop_event,
        occurrence=occurrence,
        captured_at=captured_at,
        required_fact_watermark=required_fact_watermark,
    ))


def _execute_beast_abyss_checkpoint(
    runner,
    ctx,
    payload,
    stop_event,
    *,
    checkpoint_kind,
    occurrence,
):
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
        execute_beast_abyss_auto_clear_checkpoint,
        execute_beast_abyss_formal_checkpoint,
        execute_beast_abyss_initialization_checkpoint,
        execute_beast_abyss_manual_clear_checkpoint,
    )

    executors = {
        BEAST_ABYSS_INITIALIZATION_KIND: execute_beast_abyss_initialization_checkpoint,
        BEAST_ABYSS_FORMAL_KIND: execute_beast_abyss_formal_checkpoint,
        BEAST_ABYSS_AUTO_CLEAR_KIND: execute_beast_abyss_auto_clear_checkpoint,
        BEAST_ABYSS_MANUAL_CLEAR_KIND: execute_beast_abyss_manual_clear_checkpoint,
    }
    executor = executors.get(checkpoint_kind)
    if executor is None:
        raise RuntimeError(f"未知兽渊 checkpoint：{checkpoint_kind}")
    return (yield from executor(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=occurrence,
    ))


def _execute_yunmeng_challenge_checkpoint(
    runner,
    ctx,
    payload,
    stop_event,
    *,
    occurrence,
    captured_at,
    required_fact_watermark,
):
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_challenge import (
        execute_yunmeng_challenge_checkpoint,
    )

    return (yield from execute_yunmeng_challenge_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=occurrence,
        captured_at=captured_at,
        required_fact_watermark=required_fact_watermark,
    ))


def _execute_yunmeng_active_checkpoint(
    runner,
    ctx,
    stop_event,
    *,
    occurrence,
    captured_at,
    required_fact_watermark,
):
    from backend.core.fanxiu.data_annotation.tasks.yunmeng_active import (
        execute_yunmeng_open_collection_checkpoint,
    )

    return (yield from execute_yunmeng_open_collection_checkpoint(
        runner,
        ctx,
        stop_event,
        occurrence=occurrence,
        captured_at=captured_at,
        required_fact_watermark=required_fact_watermark,
    ))


def _execute_exchange_tail_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    if occurrence.activity_type == "xutian-palace":
        from backend.core.fanxiu.data_annotation.tasks.xutian_tail import execute_xutian_exchange_tail_checkpoint
        return (yield from execute_xutian_exchange_tail_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence))
    if occurrence.activity_type == "beast-abyss":
        from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
            execute_beast_abyss_exchange_tail_checkpoint,
        )
        return (yield from execute_beast_abyss_exchange_tail_checkpoint(
            runner,
            ctx,
            payload,
            stop_event,
            occurrence=occurrence,
        ))
    if occurrence.activity_type == "magic-invasion":
        from backend.core.fanxiu.data_annotation.tasks.magic_invasion_tail import (
            execute_magic_invasion_tail_checkpoint,
        )
        return (yield from execute_magic_invasion_tail_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence
        ))
    if occurrence.activity_type == "yunmeng-trial":
        from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import execute_yunmeng_tail_job
        return (yield from execute_yunmeng_tail_job(runner, ctx, payload, stop_event))
    if occurrence.activity_type == "xianyuan-duokui":
        from backend.core.fanxiu.data_annotation.tasks.xianyuan_duokui_tail import (
            execute_xianyuan_duokui_tail_checkpoint,
        )
        return (yield from execute_xianyuan_duokui_tail_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence
        ))
    if occurrence.activity_type == "tiandi-yiju":
        from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju_tail import (
            execute_tiandi_yiju_exchange_tail_checkpoint,
        )
        return (yield from execute_tiandi_yiju_exchange_tail_checkpoint(
            runner,
            ctx,
            payload,
            stop_event,
            occurrence=occurrence,
        ))
    raise RuntimeError(f"{occurrence.activity_type} 尚无兑换收尾执行适配器")


def _parse_retry_at(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=job_now().astimezone().tzinfo)
    return parsed


def _execute_xianmeng_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from backend.core.fanxiu.activity.daily_activity_job_registry import XIANMENG_STAMINA_SWEEPS
    options = dict(payload)
    options.pop("__scheduler_task_id", None)
    options.update({
        "manage_schedule": False,
        "schedule_tail_from_daily_activity_list": False,
        "event_tail_date": job_now().astimezone().date().isoformat(),
        "event_tail_times": [f"{hour:02d}:{minute:02d}" for hour, minute in XIANMENG_STAMINA_SWEEPS],
        "daily_end_time": "22:00",
    })
    result = yield from runner._execute_daily_xianmeng_task(ctx, stop_event, options)
    if options.get("_xianmeng_day_complete_reason"):
        return {"status": "completed", "reason": "all_opponents_defeated",
                "message": options["_xianmeng_day_complete_reason"]}
    retry_at = _parse_retry_at(options.get("_xianmeng_next_time"))
    if retry_at is not None:
        return {
            "status": "pending",
            "message": f"仙盟内部执行 {result}，等待父玩法榜在 {retry_at:%H:%M:%S} 复查",
            "retry_at": retry_at.isoformat(timespec="seconds"),
        }
    return {"status": "completed", "message": f"仙盟内部执行 {result}"}


def _execute_tiandi_yiju_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from backend.core.fanxiu.data_annotation.tasks.tiandi_yiju import (
        execute_tiandi_yiju_checkpoint,
    )
    return (yield from execute_tiandi_yiju_checkpoint(
        runner, ctx, payload, stop_event, occurrence=occurrence
    ))


def _execute_resource_checkpoint(
    runner, ctx, payload, stop_event, *, checkpoint_kind, occurrence
):
    options = dict(payload)
    options.pop("__scheduler_task_id", None)
    if checkpoint_kind == RESOURCE_FREE_GIFT_KIND:
        from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
            run_resource_rank_daily_gift_flow,
        )
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        return (yield from run_resource_rank_daily_gift_flow(
            context,
            manage_schedule=False,
            expected_activity_type=occurrence.activity_type,
            expected_activity_id=occurrence.activity_id,
        ))
    if checkpoint_kind == LINGZHUANG_STRENGTHENING_KIND:
        from backend.core.fanxiu.activity.ranking_reconcile import seed_ranking_occurrence
        from backend.core.fanxiu.data_annotation.tasks.lingzhuang_strengthening import execute_lingzhuang_strengthening_task
        from backend.db import engine

        if occurrence.cross_count != 1:
            raise RuntimeError("灵装化道自动强化仅授权服内榜")
        with Session(engine) as session:
            activity = seed_ranking_occurrence(session, occurrence, captured_at=job_now().isoformat())
            activity_id = activity.id
        result = yield from execute_lingzhuang_strengthening_task(
            runner, ctx, {"activity_id": activity_id, "target_tier": 12, "max_clicks": 200}, stop_event,
        )
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        yield from context.go_scene(34)
        return {**result, "status": "completed" if result.get("ok") else "blocked"}
    if checkpoint_kind == DANDAO_RESOURCE_USE_KIND:
        from backend.core.fanxiu.data_annotation.tasks.dandao_resource_use import (
            run_dandao_resource_use_flow,
        )
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        return (yield from run_dandao_resource_use_flow(
            context, activity_id=occurrence.activity_id, now=job_now(),
        ))
    if checkpoint_kind == DANDAO_TAKE_MEDICINE_KIND:
        from backend.core.fanxiu.data_annotation.tasks.take_medicine_batch import run_take_medicine_batch_flow
        from backend.db import engine

        # A separate durable occurrence checkpoint also covers deployments
        # where today's alchemy checkpoint was completed before medicine existed.
        with Session(engine) as session:
            completed = completed_ranking_checkpoint_keys(session, family="resource_rank")
        if not any(key[0] == occurrence.instance_key and key[1] == DANDAO_RESOURCE_USE_KIND
                   for key in completed):
            return {"status": "blocked", "message": "服用丹药等待本期炼丹任务完成"}
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        return (yield from run_take_medicine_batch_flow(context))
    if checkpoint_kind == DANDAO_REWARDS_KIND:
        from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import (
            run_dandao_task_rewards_flow,
        )
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        result = yield from run_dandao_task_rewards_flow(
            context,
            expected_activity_id=occurrence.activity_id,
            max_claims=int(options.get("max_claims") or 20),
            manage_schedule=False,
        )
        if result.get("boundary") == "no_claimable_progress":
            retry_at = _parse_retry_at(result.get("next_time"))
            if retry_at is not None:
                result.update(status="pending", retry_at=retry_at.isoformat(timespec="seconds"))
        return result
    if checkpoint_kind == YUANDING_GIFT_KIND:
        return (yield from runner._execute_yuanding_sansheng_daily_gift_task(
            ctx, stop_event, {**options, "manage_schedule": False}
        ))
    if checkpoint_kind in YUANDING_RESOURCE_UNIT_KIND_SET:
        # 缘定三生正式运行单元：任务奖励领取 + 自动联姻使用资源，两段连跑，最后回 #34。
        # 任务奖励在本期领完后由运行单元自己按页面「领」角标幂等跳过。
        return (yield from runner._execute_yuanding_sansheng_resource_unit(
            ctx,
            stop_event,
            {
                **options,
                "manage_schedule": False,
                "task_rewards_done": bool(options.get("yuanding_task_rewards_done")),
            },
        ))
    raise RuntimeError(f"未知资源榜 checkpoint：{checkpoint_kind}")


def _execute_family_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    family: RankingFamily,
    task_id: str,
    label: str,
) -> Iterator[Any]:
    """Run due obligations; explicit retry_pending_activity_types bypasses only
    the pending retry clock for those activity types, never completion or the
    activity's own admission checks. Ordinary scheduled runs keep retry clocks.
    """
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.db import engine

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True)
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError(f"{label} Runtime 日程不可用或不完整")
    # Keep the family boundary here as a second guard so test/probe adapters
    # that replace discovery cannot accidentally leak the sibling family.
    occurrences = tuple(
        item for item in discover_ranking_occurrences(schedule)
        if item.family == family
    )
    by_instance = {item.instance_key: item for item in occurrences}
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import record_world_discovery
    from backend.core.fanxiu.data_annotation.subtask_execution import observe_subtask, subtask_node_id
    from backend.core.fanxiu.data_annotation.subtask_tree import stage_label
    record_world_discovery(f"ranking_subtask_plan:{task_id}", {
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "occurrences": [{"raw": raw} for raw in schedule.get("items", [])],
    })
    scheduler_task_id = str(ctx.get("scheduler_task_id") or task_id)
    retry_pending_activity_types = payload.get("retry_pending_activity_types", [])
    if not isinstance(retry_pending_activity_types, list) or any(
        not isinstance(item, str) or not item for item in retry_pending_activity_types
    ):
        raise ValueError("retry_pending_activity_types 必须是非空活动类型字符串的列表")
    retry_pending_activity_types = set(retry_pending_activity_types)
    ensure_ranking_lifecycle_checkpoint_table(engine)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        # Page registration is read-only with respect to the game and must not
        # disappear when an activity's action checkpoints are still in R&D.
        sync_ranking_schedule(session, schedule, now=now, family=family)
        # Repair legacy "outside dates" terminals only when this exact live
        # occurrence proves it is preparing/open. Future starts become pending;
        # closed instances and successfully completed work remain untouched.
        from backend.core.fanxiu.activity.ranking_lifecycle_store import reopen_failed_ranking_checkpoint
        for row in list_ranking_checkpoint_rows(session, instance_keys=by_instance):
            live = by_instance.get(row.instance_key)
            if (live is not None and row.status == 'unavailable'
                    and (row.result or {}).get('terminal_reason') == 'activity_out_of_effective_dates'
                    and live.prepare_at <= now <= live.close_at):
                reopen_failed_ranking_checkpoint(
                    session, instance_key=row.instance_key, checkpoint_kind=row.checkpoint_kind,
                    business_date=row.business_date, occurrence=live, now=now,
                )
        completed = completed_ranking_checkpoint_keys(session, family=family)
        planned_due = due_ranking_checkpoints(
            occurrences,
            now=now,
            completed_keys=completed,
            production_only=True,
        )
        due = planned_due
        # Explicit maintenance replay can target one gameplay without running
        # siblings that become due while its repair is in progress.
        only_activity_types = payload.get("only_activity_types")
        if only_activity_types is not None:
            if not isinstance(only_activity_types, list) or not only_activity_types or any(
                not isinstance(value, str) or not value for value in only_activity_types
            ):
                raise ValueError("only_activity_types 必须是非空活动类型列表")
            due = tuple(c for c in due if c.activity_type in only_activity_types)
        # An active Xianmeng retry must never silently become a zero-action
        # pass that sleeps until tomorrow. Keep its durable obligation visible
        # if discovery/planning admission stops producing its checkpoint.
        planned_keys = {checkpoint.key for checkpoint in planned_due}
        checkpoint_rows = list_ranking_checkpoint_rows(session, instance_keys=by_instance)
        for row in checkpoint_rows:
            if only_activity_types is not None and row.activity_type not in only_activity_types:
                continue
            occurrence = by_instance.get(row.instance_key)
            if (
                row.checkpoint_kind == XIANMENG_ACTIVE_KIND
                and row.status == "pending"
                and occurrence is not None
                and occurrence.start_at <= now <= occurrence.end_at
                and row.business_date == now.astimezone(occurrence.start_at.tzinfo).date().isoformat()
                and (row.instance_key, row.checkpoint_kind, row.business_date) not in planned_keys
            ):
                raise RuntimeError(
                    "仙盟仍有当前活动的待复查义务，但规划未包含该 checkpoint；"
                    "保留待办，禁止把零动作写成明日再运行"
                )
        # A lawful pending business outcome remains an obligation, but its
        # future retry time must prevent re-entering the game on another
        # checkpoint's wakeup or an idempotent formal scheduling replay.
        waiting_keys = {
            (row.instance_key, row.checkpoint_kind, row.business_date)
            for row in checkpoint_rows
            if row.status == "pending" and row.retry_at
            and row.activity_type not in retry_pending_activity_types
            and (retry := _parse_retry_at(row.retry_at)) is not None and retry > now
        }
        due = tuple(checkpoint for checkpoint in due if checkpoint.key not in waiting_keys)

    # Only a normal business outcome may advance next_time. Technical failures
    # propagate to the Scheduler, which owns engineering retries and AI stops.
    xianmeng_counts: dict[str, int] = {}
    for checkpoint in due:
        if checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND:
            xianmeng_counts[checkpoint.business_date] = (
                xianmeng_counts.get(checkpoint.business_date, 0) + 1
            )
    for checkpoint in due:
        if stop_event.is_set():
            raise InterruptedError()
        occurrence = by_instance.get(checkpoint.instance_key)
        if occurrence is None and checkpoint.checkpoint_kind == MAGIC_MAIL_KIND:
            occurrence = next(
                (
                    item
                    for item in occurrences
                    if item.runtime_id == checkpoint.runtime_id
                    and item.activity_id == checkpoint.activity_id
                ),
                None,
            )
        if occurrence is None:
            raise RuntimeError(
                f"玩法榜 checkpoint 找不到所属实例：{checkpoint.instance_key}"
            )
        try:
            with observe_subtask(
                scheduler_task_id, str(payload.get("__scheduler_attempt_id") or ""),
                subtask_node_id(scheduler_task_id, *checkpoint.key), stage_label(checkpoint.checkpoint_kind),
                log=lambda message: runner._log("info", message),
            ):
                if (
                    checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND
                    and xianmeng_counts.get(checkpoint.business_date, 0) != 1
                ):
                    raise RuntimeError(
                        "同一业务日发现多个仙盟榜实例，无法证明唯一页面归属，拒绝执行"
                    )
                if checkpoint.checkpoint_kind == SHENGXIAN_PEAK_FINAL_KIND:
                    from .shengxian_hui import execute_shengxian_peak_final_checkpoint
                    result = yield from execute_shengxian_peak_final_checkpoint(
                        runner, ctx, stop_event, occurrence=occurrence)
                elif checkpoint.checkpoint_kind == BEAST_ABYSS_REGISTRATION_KIND:
                    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_registration import register_beast_abyss
                    context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
                    result = yield from register_beast_abyss(context, occurrence=occurrence, now=now)
                elif checkpoint.checkpoint_kind == DAILY_RECONCILE_KIND:
                    capability = RANKING_CAPABILITY_STATUS.get(occurrence.activity_type)
                    if capability == "observed_unhandled" or occurrence.activity_type == "xianmeng-competition":
                        result = {
                            "status": "retained",
                            "message": f"{occurrence.activity_type} 已发现，能力状态 {capability or 'internal_adapter'}",
                            "capability": capability or "internal_adapter",
                        }
                    elif occurrence.activity_type == "beast-abyss":
                        from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
                            execute_beast_abyss_daily_reconcile_checkpoint,
                        )

                        result = yield from execute_beast_abyss_daily_reconcile_checkpoint(
                            runner,
                            ctx,
                            stop_event,
                            occurrence=occurrence,
                            captured_at=now,
                            required_fact_watermark=checkpoint.due_at,
                        )
                    else:
                        if occurrence.activity_type in {"xiling-zhengwu", "lingzhuang-huadao"} and occurrence.start_at <= now <= occurrence.end_at:
                            from .resource_rank_page import refresh_resource_rank_page
                            context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
                            yield from refresh_resource_rank_page(context, occurrence=occurrence, now=now)
                        if occurrence.activity_type == "lianti-faxiang":
                            # Runtime 只在客户端打开过榜单页后才加载本期个人榜；
                            # 每日对账前显式加载一次，失败不掩盖既有事实。
                            from backend.core.fanxiu.activity.ranking_reconcile import (
                                seed_ranking_occurrence,
                            )
                            from backend.core.fanxiu.data_annotation.tasks.lianti_faxiang import (
                                refresh_lianti_faxiang_rank_page,
                            )

                            with Session(engine) as session:
                                seeded = seed_ranking_occurrence(
                                    session,
                                    occurrence,
                                    captured_at=now.isoformat(timespec="seconds"),
                                )
                                rank_activity_id = int(seeded.game_rank_activity_id or 0)
                                session.commit()
                            if rank_activity_id <= 0:
                                raise RuntimeError(
                                    "炼体法相每日对账：所选实例缺少个人榜绑定身份"
                                )
                            context = runner._behavior_tree_context(
                                ctx,
                                ctx.get("asset_tree_path"),
                                stop_event=stop_event,
                            )
                            yield from refresh_lianti_faxiang_rank_page(
                                context,
                                occurrence=occurrence,
                                now=now,
                                rank_activity_id=rank_activity_id,
                            )
                        with Session(engine) as session:
                            result = reconcile_ranking_occurrence(
                                session,
                                occurrence,
                                captured_at=now.isoformat(timespec="seconds"),
                                required_fact_watermark=checkpoint.due_at,
                            )
                    # Reconcile's blocked outcome means missing required Runtime
                    # facts, not lawful business waiting. Surface it through the
                    # same failure/evidence boundary as an exception; otherwise
                    # the family Job records success and retries forever.
                    if result.get("status") == "blocked":
                        raise RuntimeError(str(result.get("message") or "榜单对账所需事实不可用"))
                elif checkpoint.checkpoint_kind == MAGIC_INITIALIZATION_KIND:
                    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_initialization import (
                        execute_magic_invasion_initialization_checkpoint,
                    )

                    result = yield from execute_magic_invasion_initialization_checkpoint(
                        runner,
                        ctx,
                        stop_event,
                        occurrence=occurrence,
                        captured_at=now,
                        required_fact_watermark=checkpoint.due_at,
                    )
                elif checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND:
                    if not exchange_tail_executor_is_production(checkpoint.activity_type):
                        from backend.core.fanxiu.data_annotation.ranking_escalation import RankingCapabilityMissing
                        raise RankingCapabilityMissing(
                            f'{checkpoint.activity_type} 已到兑换收尾时间，但缺少已验收执行器；'
                            f'兑换截止 {occurrence.close_at.isoformat()}，禁止静默跳过')
                    result = yield from _execute_exchange_tail_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == MAGIC_ACTIVE_KIND:
                    result = yield from _execute_magic_active_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == MAGIC_FORMAL_KIND:
                    from .magic_invasion_reward_target import execute_magic_invasion_reward_checkpoint
                    result = yield from execute_magic_invasion_reward_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == MAGIC_MAIL_KIND:
                    result = yield from _execute_magic_mail_checkpoint(
                        runner, ctx, payload, stop_event, checkpoint=checkpoint
                    )
                elif checkpoint.checkpoint_kind == XUTIAN_ACTIVE_KIND:
                    result = yield from _execute_xutian_active_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == XUTIAN_OPEN_COLLECTION_KIND:
                    result = yield from _execute_xutian_open_collection_checkpoint(
                        runner,
                        ctx,
                        stop_event,
                        occurrence=occurrence,
                        captured_at=now,
                        required_fact_watermark=checkpoint.due_at,
                    )
                elif checkpoint.checkpoint_kind in {
                    BEAST_ABYSS_FORMAL_KIND,
                    BEAST_ABYSS_INITIALIZATION_KIND,
                    BEAST_ABYSS_AUTO_CLEAR_KIND,
                    BEAST_ABYSS_MANUAL_CLEAR_KIND,
                }:
                    result = yield from _execute_beast_abyss_checkpoint(
                        runner,
                        ctx,
                        payload,
                        stop_event,
                        checkpoint_kind=checkpoint.checkpoint_kind,
                        occurrence=occurrence,
                    )
                elif checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND:
                    result = yield from _execute_xianmeng_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == TIANDI_YIJU_ACTIVE_KIND:
                    result = yield from _execute_tiandi_yiju_checkpoint(
                        runner, ctx, payload, stop_event, occurrence=occurrence
                    )
                elif checkpoint.checkpoint_kind == YUNMENG_ACTIVE_KIND:
                    result = yield from _execute_yunmeng_active_checkpoint(
                        runner,
                        ctx,
                        stop_event,
                        occurrence=occurrence,
                        captured_at=now,
                        required_fact_watermark=checkpoint.due_at,
                    )
                elif checkpoint.checkpoint_kind in {
                    YUNMENG_CHALLENGE_KIND,
                    YUNMENG_CHALLENGE_EVENING_KIND,
                }:
                    result = yield from _execute_yunmeng_challenge_checkpoint(
                        runner,
                        ctx,
                        payload,
                        stop_event,
                        occurrence=occurrence,
                        captured_at=now,
                        required_fact_watermark=checkpoint.due_at,
                    )
                else:
                    result = yield from _execute_resource_checkpoint(
                        runner,
                        ctx,
                        payload,
                        stop_event,
                        checkpoint_kind=checkpoint.checkpoint_kind,
                        occurrence=occurrence,
                    )
                if not isinstance(result, dict):
                    result = {"status": "completed", "message": str(result or "")}
                status = str(result.get("status") or "completed")
                if status == "error":
                    raise RuntimeError(str(result.get("message") or "榜单 checkpoint 执行失败"))
                retry_at = _parse_retry_at(result.get("retry_at"))
                if status in {"blocked", "pending"} and retry_at is None:
                    status, retry_at = _default_retry_policy(
                        status=status,
                        checkpoint=checkpoint,
                        occurrence=occurrence,
                        now=now,
                    )
                # Persist and aggregate the same resolved checkpoint status.
                result = {**result, "status": status}
                with Session(engine) as session:
                    record_ranking_checkpoint_result(
                        session,
                        checkpoint,
                        status=status,
                        message=str(result.get("message") or ""),
                        result=result,
                        retry_at=retry_at,
                        completed_at=now if status in {"completed", "retained", "unavailable"} else None,
                    )
                results.append({"checkpoint": checkpoint.as_dict(), "result": result})
        except (InterruptedError, KeyboardInterrupt):
            raise
        except Exception as exc:
            # Preserve the first failure and GUI surface. Do not terminalize it
            # or proceed to sibling actions; a new attempt starts from facts.
            with Session(engine) as session:
                record_ranking_checkpoint_result(
                    session,
                    checkpoint,
                    status="error",
                    message=str(exc),
                    result={"error_type": type(exc).__name__},
                )
                try:
                    from backend.core.fanxiu.data_annotation.ranking_escalation import report_ranking_failure
                    try:
                        failure_frame = runner._behavior_tree_context(ctx, stop_event=stop_event).cur_frame(update=True)
                    except Exception:
                        failure_frame = None
                    repair = report_ranking_failure(
                        session, checkpoint=checkpoint, occurrence=occurrence, error=exc,
                        task_id=scheduler_task_id, entry_id=str(ctx.get('entry_id') or ''),
                        frame_data_url=failure_frame,
                    )
                    runner._log('warning', f"榜单异常升级：{repair.get('status')}；{repair.get('dispatch_error') or repair.get('dispatch_id') or str(exc)}")
                except Exception as dispatch_error:
                    # Preserve the business exception even if evidence or AI
                    # transport is broken, and make that second fault visible.
                    runner._log('warning', f'榜单异常上报失败：{dispatch_error}')
            raise

    with Session(engine) as session:
        completed = completed_ranking_checkpoint_keys(session, family=family)
        next_time = next_ranking_lifecycle_time(
            occurrences,
            now=now,
            completed_keys=completed,
            retry_times=ranking_checkpoint_retry_times(session, family=family),
            production_only=True,
        )
    runner._persist_scheduler_task_next_time(scheduler_task_id, next_time)
    pending = [
        item
        for item in results
        if item["result"].get("status") in {"error", "blocked", "pending"}
    ]
    unavailable = [
        item
        for item in results
        if item["result"].get("status") == "unavailable"
    ]
    succeeded = [
        item
        for item in results
        if item["result"].get("status") in {"completed", "retained"}
    ]
    message = (
        f"{label}：处理 {len(results)} 个 checkpoint，成功 {len(succeeded)}，"
        f"待重试 {len(pending)}，不可用 {len(unavailable)}；"
        f"下次 {next_time:%Y-%m-%d %H:%M:%S}"
    )
    if pending or unavailable:
        details = []
        for item in [*pending, *unavailable]:
            checkpoint = item["checkpoint"]
            outcome = item["result"]
            reason = str(outcome.get("message") or outcome.get("reason") or "未提供原因").strip()
            details.append(
                f"{checkpoint.get('activity_type')}/{checkpoint.get('checkpoint_kind')}"
                f"[{outcome.get('status')}]：{reason[:160]}"
            )
        message += "；" + "；".join(details[:3])
    runner._log("warning" if pending or unavailable else "success", message)
    return {
        # Business waiting is a successful Scheduler pass; a
        # terminal unavailable outcome is not and must not be reported as a
        # clean success.
        "result": "partial" if unavailable else "success",
        "message": message,
        "performed_actions": bool(results),
        "family": family,
        "successful_checkpoint_count": len(succeeded),
        "pending_checkpoint_count": len(pending),
        "unavailable_checkpoint_count": len(unavailable),
        "checkpoint_results": results,
    }


def execute_ranking_lifecycle_job(runner, ctx, payload, stop_event):
    return (yield from _execute_family_job(
        runner, ctx, payload, stop_event,
        family="gameplay_rank", task_id=RANKING_LIFECYCLE_TASK_ID, label="玩法榜",
    ))


def execute_beast_abyss_lifecycle_rnd_cell(runner, ctx, payload, stop_event):
    """Explicitly validate today's Beast Abyss collect -> model -> formal flow.

    This remains outside production scheduling. Resolve one occurrence once,
    carry its identity through every phase, and never advance past a pending
    phase. Existing settled measurements are resumed by the phase provider.
    Ordinary task returns mean trigger success in the framework, so incomplete
    R&D phases must raise after their provider has persisted recovery state.
    """
    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
        execute_beast_abyss_daily_reconcile_checkpoint,
        execute_beast_abyss_initialization_checkpoint,
        execute_beast_abyss_formal_checkpoint,
        read_beast_abyss_challenge_state,
    )

    payload = {**payload, "stay_in_activity": True}
    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    if not (time(10, 0) <= now.timetz().replace(tzinfo=None) < time(21, 30)):
        raise RuntimeError("兽渊生命周期研发只能在10:00-21:30自动挑战窗口内运行")
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True, force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("兽渊生命周期研发：Runtime日程不可用或不完整")
    matches = tuple(
        item for item in discover_ranking_occurrences(schedule)
        if item.family == "gameplay_rank"
        and item.activity_type == "beast-abyss"
        and item.start_at <= now <= item.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(f"兽渊生命周期研发无法唯一定位当前开放实例：matches={len(matches)}")
    occurrence = matches[0]
    phases = {}
    # A pending batch owns the current GUI surface. Revisiting the shop would
    # destroy the terminal/start evidence required by its recovery protocol.
    state = read_beast_abyss_challenge_state(occurrence)
    pending = state.get("initialization", {}).get("pending_batch")
    formal_pending = state.get("formal", {}).get("pending_batch")
    if pending is None and formal_pending is None:
        phases["reconcile"] = yield from execute_beast_abyss_daily_reconcile_checkpoint(
            runner, ctx, stop_event, occurrence=occurrence, captured_at=now,
            stay_in_activity=True,
            required_fact_watermark=max(
                occurrence.start_at, now.replace(hour=0, minute=30, second=0, microsecond=0),
            ),
        )
        if phases["reconcile"].get("status") != "completed":
            raise RuntimeError(
                f"兽渊采集阶段未完成 [{phases['reconcile'].get('status', 'unknown')}]："
                f"{phases['reconcile'].get('message') or '实例事实不完整'}"
            )
    else:
        phases["reconcile"] = {
            "status": "retained", "message": "保留未结批次现场，复用本期宝阁事实恢复初始化",
        }
    if stop_event.is_set():
        raise InterruptedError()
    if formal_pending:
        phases["initialization"] = {"status": "completed", "message": "保留正式批次现场；正式入口核对初始化证据"}
    else:
        phases["initialization"] = yield from execute_beast_abyss_initialization_checkpoint(
            runner, ctx, payload, stop_event, occurrence=occurrence)
    if phases["initialization"].get("outcome") == "pass":
        return {**phases["initialization"], "phases": phases}
    if phases["initialization"].get("status") != "completed":
        raise RuntimeError(
            f"兽渊初始化阶段未完成 [{phases['initialization'].get('status', 'unknown')}]："
            f"{phases['initialization'].get('message') or '初始化未完成，已保存状态供恢复'}"
        )
    if stop_event.is_set():
        raise InterruptedError()
    phases["formal"] = yield from execute_beast_abyss_formal_checkpoint(
        runner, ctx, payload, stop_event, occurrence=occurrence,
    )
    if phases["formal"].get("outcome") in {"pass", "deferred"}:
        return {**phases["formal"], "phases": phases}
    if phases["formal"].get("status") != "completed":
        raise RuntimeError(
            f"兽渊正式阶段未完成 [{phases['formal'].get('status', 'unknown')}]："
            f"{phases['formal'].get('message') or '正式运行未完成'}"
        )
    return {**phases["formal"], "phases": phases}


def execute_beast_abyss_initialization_rnd_cell(runner, ctx, payload, stop_event):
    """Run only the current Beast Abyss initialization in an explicit R&D Cell."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
        execute_beast_abyss_initialization_checkpoint,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    wall_clock = now.timetz().replace(tzinfo=None)
    if not (time(10, 0) <= wall_clock < time(21, 30)):
        raise RuntimeError("兽渊初始化研发只能在10:00-21:30自动挑战窗口内运行")
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("兽渊初始化研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "beast-abyss"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"兽渊初始化研发无法唯一定位当前开放实例：matches={len(matches)}"
        )
    return (yield from execute_beast_abyss_initialization_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=matches[0],
    ))


def execute_magic_invasion_initialization_rnd_cell(runner, ctx, payload, stop_event):
    """Initialize only the unique current Magic occurrence in an explicit R&D Cell."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_initialization import (
        execute_magic_invasion_initialization_checkpoint,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道初始化研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "magic-invasion"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"魔道初始化研发无法唯一定位当前开放实例：matches={len(matches)}"
        )
    occurrence = matches[0]
    return (yield from execute_magic_invasion_initialization_checkpoint(
        runner,
        ctx,
        stop_event,
        occurrence=occurrence,
        captured_at=now,
        required_fact_watermark=occurrence.start_at.replace(
            hour=0,
            minute=30,
            second=0,
            microsecond=0,
        ),
    ))


def complete_xianmeng_defeated_day_from_runtime() -> dict[str, Any]:
    """Reconcile today's no-opponent terminal from fresh facts, without GUI.

    R&D can finalize this proven terminal while staying on the battlefield.
    The ordinary executor uses the same eligibility predicate. Only this
    occurrence's current-day checkpoint is completed; siblings stay untouched.
    """
    from backend.db import engine
    from backend.core.fanxiu.activity.runtime_schedule import read_fanxiu_activity_runtime_schedule
    from backend.core.fanxiu.activity.ranking_lifecycle import checkpoints_for_occurrence
    from backend.core.fanxiu.data_annotation.kernel_scheduler_control import set_scheduler_task_next_time
    from backend.core.fanxiu.activity.xianmeng_targets import read_xianmeng_attackable_targets

    now = job_now().astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(allow_discovery=True, force_refresh=True)
    if not (schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("仙盟日程不完整，不能结算当日")
    occurrences = tuple(o for o in discover_ranking_occurrences(schedule) if o.family == "gameplay_rank")
    active = [o for o in occurrences if o.activity_type == "xianmeng-competition"
              and o.start_at <= now <= o.end_at]
    if len(active) != 1:
        raise RuntimeError("当前仙盟活动不唯一，不能结算当日")
    snapshot = read_xianmeng_attackable_targets()
    plan = snapshot["fallback_plan"]
    if not plan["all_opponents_defeated"]:
        return {"status": "pending", "message": "尚未证明所有非友军积分归零，未改动完成状态"}
    occurrence = active[0]
    day = now.astimezone(occurrence.start_at.tzinfo).date()
    checkpoint = next(c for c in checkpoints_for_occurrence(occurrence, business_day=day)
                      if c.checkpoint_kind == XIANMENG_ACTIVE_KIND)
    result = {"status": "completed", "reason": "all_opponents_defeated",
              "message": "所有非友军阵柱积分为0，当日无对手；今日完成，不再复查体力"}
    ensure_ranking_lifecycle_checkpoint_table(engine)
    with Session(engine) as session:
        completed = completed_ranking_checkpoint_keys(session, family="gameplay_rank")
        already_completed = checkpoint.key in completed
        if not already_completed:
            record_ranking_checkpoint_result(session, checkpoint, status="completed",
                message=result["message"], result=result,
                evidence={"opponents": plan["opponents"], "captured_at": snapshot.get("captured_at")},
                completed_at=now)
        completed = completed_ranking_checkpoint_keys(session, family="gameplay_rank")
        next_time = next_ranking_lifecycle_time(occurrences, now=now, completed_keys=completed,
            retry_times=ranking_checkpoint_retry_times(session, family="gameplay_rank"), production_only=True)
    set_scheduler_task_next_time(RANKING_LIFECYCLE_TASK_ID, next_time)
    return {**result, "business_date": day.isoformat(), "already_completed": already_completed,
            "parent_next_time": next_time.isoformat()}


def execute_xianmeng_active_rnd_cell(runner, ctx, payload, stop_event):
    """Explicitly run the unique current 仙盟 gameplay occurrence once (R&D).

    Reads a complete fresh Runtime schedule, selects the only currently open
    ``xianmeng-competition`` ``gameplay_rank`` occurrence, and delegates to the
    existing ``_execute_xianmeng_checkpoint``.  That executor performs the real
    仙盟 challenge actions; it does not advance the parent gameplay ranking
    checkpoint. It preserves the existing executor's stamina, cooldown and
    bounded-round outcomes, including any ``pending`` retry time. Allowlists are not
    changed; this thin entry exists only for explicit live R&D acceptance.
    """

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("仙盟正式运行研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "xianmeng-competition"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"仙盟正式运行研发无法唯一定位当前开放实例：matches={len(matches)}"
        )
    return (yield from _execute_xianmeng_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=matches[0],
    ))


def execute_magic_invasion_active_rnd_cell(runner, ctx, payload, stop_event):
    """Run the unique current Magic occurrence's 3x500 exploration compound once."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_compound import (
        execute_magic_invasion_compound_checkpoint,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道正式运行研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "magic-invasion"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"魔道正式运行研发无法唯一定位当前开放实例：matches={len(matches)}"
        )
    return (yield from execute_magic_invasion_compound_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=matches[0],
    ))


def _magic_occurrence_for_auto(occurrence) -> Any:
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion import (
        MagicInvasionOccurrence,
    )

    return MagicInvasionOccurrence(
        occurrence_id=occurrence.runtime_id,
        activity_id=int(occurrence.activity_id),
        runtime_id=int(occurrence.runtime_id),
        start_time_ms=int(occurrence.start_at.timestamp() * 1000),
        end_time_ms=int(occurrence.end_at.timestamp() * 1000),
        server_count=int(occurrence.cross_count),
        mode="cross" if int(occurrence.cross_count) > 1 else "server",
    )


def execute_magic_invasion_native_auto_rnd_cell(runner, ctx, payload, stop_event):
    """Run the current Magic occurrence's native auto-exorcism batches (R&D)."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_native_auto import (
        clear_unstarted_magic_invasion_auto_pending,
        run_magic_invasion_auto_batch,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("魔道自动除魔研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "magic-invasion"
        and occurrence.start_at <= now <= occurrence.end_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"魔道自动除魔研发无法唯一定位当前开放实例：matches={len(matches)}"
        )
    magic_occurrence = _magic_occurrence_for_auto(matches[0])
    batches = max(1, int(payload.get("batches") or 1))
    context = runner._behavior_tree_context(ctx, stop_event=stop_event)
    results: list[dict[str, Any]] = []
    if bool(payload.get("clear_stale_pending")):
        results.append({
            "status": "cleared_pending",
            "result": clear_unstarted_magic_invasion_auto_pending(
                magic_occurrence,
                reason=str(
                    payload.get("clear_reason")
                    or "研发恢复：导航期失败，未点击开启自动"
                ),
            ),
        })
    for _batch in range(batches):
        if stop_event.is_set():
            raise InterruptedError()
        result = yield from run_magic_invasion_auto_batch(
            context,
            magic_occurrence,
            count=int(payload.get("count") or 100),
        )
        results.append(result)
        if str(result.get("status") or "") != "settled":
            break
    return {"status": "completed", "occurrence": magic_occurrence.occurrence_id, "results": results}


def execute_beast_abyss_rank_refresh_rnd_cell(runner, ctx, payload, stop_event):
    """Refresh the current Beast Abyss rank tabs without challenge or exchange."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.core.fanxiu.data_annotation.tasks.beast_abyss_active import (
        execute_beast_abyss_rank_refresh_probe,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("兽渊榜单刷新研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "beast-abyss"
        and occurrence.start_at <= now <= occurrence.close_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"兽渊榜单刷新研发无法唯一定位本期实例：matches={len(matches)}"
        )
    return (yield from execute_beast_abyss_rank_refresh_probe(
        runner,
        ctx,
        stop_event,
        occurrence=matches[0],
    ))


def execute_beast_abyss_exchange_tail_rnd_cell(runner, ctx, payload, stop_event):
    """Settle the unique closed Beast Abyss occurrence still in its grace period."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("兽渊兑换收尾研发：Runtime 日程不可用或不完整")
    matches = tuple(
        occurrence
        for occurrence in discover_ranking_occurrences(schedule)
        if occurrence.family == "gameplay_rank"
        and occurrence.activity_type == "beast-abyss"
        and occurrence.end_at < now < occurrence.close_at
    )
    if len(matches) != 1:
        raise RuntimeError(
            f"兽渊兑换收尾研发无法唯一定位结算期实例：matches={len(matches)}"
        )
    return (yield from _execute_exchange_tail_checkpoint(
        runner,
        ctx,
        payload,
        stop_event,
        occurrence=matches[0],
    ))


def execute_resource_ranking_job(runner, ctx, payload, stop_event):
    return (yield from _execute_family_job(
        runner, ctx, payload, stop_event,
        family="resource_rank", task_id=RESOURCE_RANKING_TASK_ID, label="资源榜",
    ))


__all__ = [
    "PRODUCTION_EXCHANGE_TAIL_EXECUTOR_ACTIVITY_TYPES",
    "exchange_tail_executor_is_production",
    "execute_beast_abyss_initialization_rnd_cell",
    "execute_beast_abyss_lifecycle_rnd_cell",
    "execute_magic_invasion_initialization_rnd_cell",
    "execute_magic_invasion_active_rnd_cell",
    "execute_magic_invasion_native_auto_rnd_cell",
    "execute_beast_abyss_exchange_tail_rnd_cell",
    "execute_beast_abyss_rank_refresh_rnd_cell",
    "execute_ranking_lifecycle_job",
    "execute_resource_ranking_job",
]
