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
    DAILY_RECONCILE_KIND,
    DANDAO_REWARDS_KIND,
    EXCHANGE_TAIL_KIND,
    MAGIC_INITIALIZATION_KIND,
    MAGIC_ACTIVE_KIND,
    MAGIC_MAIL_KIND,
    RANKING_CAPABILITY_STATUS,
    PRODUCTION_GAMEPLAY_EXCHANGE_TAIL_ACTIVITY_TYPES,
    RANKING_LIFECYCLE_TASK_ID,
    RESOURCE_FREE_GIFT_KIND,
    LINGZHUANG_STRENGTHENING_KIND,
    RESOURCE_RANKING_TASK_ID,
    TIANDI_YIJU_ACTIVE_KIND,
    XUTIAN_ACTIVE_KIND,
    XIANMENG_ACTIVE_KIND,
    YUANDING_GIFT_KIND,
    RankingFamily,
    discover_ranking_occurrences,
    due_ranking_checkpoints,
    next_ranking_lifecycle_time,
)
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    completed_ranking_checkpoint_keys,
    ensure_ranking_lifecycle_checkpoint_table,
    list_ranking_checkpoint_rows,
    ranking_checkpoint_retry_times,
    record_ranking_checkpoint_result,
)
from backend.core.fanxiu.activity.ranking_reconcile import reconcile_ranking_occurrence, sync_ranking_schedule
from backend.core.fanxiu.data_annotation.effective_time import job_now


CHECKPOINT_RETRY_DELAY = timedelta(minutes=10)
MAX_DEFAULT_CHECKPOINT_ATTEMPTS = 3

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
    prior_attempt_count: int,
) -> tuple[str, datetime | None]:
    """Bound implicit retries and align pre-start work with the real window."""

    if status not in {"error", "blocked", "pending"}:
        return status, None
    if checkpoint.checkpoint_kind in {
        DAILY_RECONCILE_KIND,
        MAGIC_INITIALIZATION_KIND,
    } and now < occurrence.start_at:
        return status, occurrence.start_at
    if now > occurrence.close_at or prior_attempt_count + 1 >= MAX_DEFAULT_CHECKPOINT_ATTEMPTS:
        return "unavailable", None
    return status, now + CHECKPOINT_RETRY_DELAY


def _execute_magic_active_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_compound import (
        execute_magic_invasion_compound_checkpoint,
    )
    return (yield from execute_magic_invasion_compound_checkpoint(
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


def _execute_exchange_tail_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
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
    options = dict(payload)
    options.pop("__scheduler_task_id", None)
    options.update({
        "manage_schedule": False,
        "schedule_tail_from_daily_activity_list": False,
        "event_tail_date": job_now().astimezone().date().isoformat(),
        "event_tail_times": ["21:10", "21:50"],
        "daily_end_time": "22:00",
    })
    result = yield from runner._execute_daily_xianmeng_task(ctx, stop_event, options)
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
    if checkpoint_kind == DANDAO_REWARDS_KIND:
        from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import (
            run_dandao_task_rewards_flow,
        )
        context = runner._behavior_tree_context(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        result = yield from run_dandao_task_rewards_flow(
            context,
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
    scheduler_task_id = str(ctx.get("scheduler_task_id") or task_id)
    ensure_ranking_lifecycle_checkpoint_table(engine)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        # Page registration is read-only with respect to the game and must not
        # disappear when an activity's action checkpoints are still in R&D.
        sync_ranking_schedule(session, schedule, now=now, family=family)
        completed = completed_ranking_checkpoint_keys(session, family=family)
        prior_attempt_counts = {
            (row.instance_key, row.checkpoint_kind, row.business_date): int(
                row.attempt_count or 0
            )
            for row in list_ranking_checkpoint_rows(session)
            if row.family == family
        }
        planned_due = due_ranking_checkpoints(
            occurrences,
            now=now,
            completed_keys=completed,
            production_only=True,
        )
        deferred_exchange_tails = tuple(
            checkpoint
            for checkpoint in planned_due
            if checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND
            and not exchange_tail_executor_is_production(checkpoint.activity_type)
        )
        due = tuple(
            checkpoint
            for checkpoint in planned_due
            if checkpoint not in deferred_exchange_tails
        )
        initial_next_time = next_ranking_lifecycle_time(
            occurrences,
            now=now,
            completed_keys=completed,
            retry_times=ranking_checkpoint_retry_times(session, family=family),
            production_only=True,
        )

    # Persist a future wake before running any checkpoint.  Ranking checkpoints
    # may spend a long time in GUI flows, and the external attempt reaper can
    # observe the Cell terminal before the dispatching thread writes its final
    # projection.  Leaving the claimed Job's old (often already-consumed)
    # trigger in place made that race able to strand ranking-lifecycle at
    # ``next_time = null`` even though the Cell result reported a future wake.
    # The final write below still refines this baseline with retry_at values
    # produced by the current attempt.
    runner._persist_scheduler_task_next_time(scheduler_task_id, initial_next_time)
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
            if (
                checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND
                and xianmeng_counts.get(checkpoint.business_date, 0) != 1
            ):
                raise RuntimeError(
                    "同一业务日发现多个仙盟榜实例，无法证明唯一页面归属，拒绝执行"
                )
            if checkpoint.checkpoint_kind == DAILY_RECONCILE_KIND:
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
                    with Session(engine) as session:
                        result = reconcile_ranking_occurrence(
                            session,
                            occurrence,
                            captured_at=now.isoformat(timespec="seconds"),
                            required_fact_watermark=checkpoint.due_at,
                        )
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
                result = yield from _execute_exchange_tail_checkpoint(
                    runner, ctx, payload, stop_event, occurrence=occurrence
                )
            elif checkpoint.checkpoint_kind == MAGIC_ACTIVE_KIND:
                result = yield from _execute_magic_active_checkpoint(
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
            retry_at = _parse_retry_at(result.get("retry_at"))
            if status in {"error", "blocked", "pending"} and retry_at is None:
                status, retry_at = _default_retry_policy(
                    status=status,
                    checkpoint=checkpoint,
                    occurrence=occurrence,
                    now=now,
                    prior_attempt_count=prior_attempt_counts.get(checkpoint.key, 0),
                )
                if status == "unavailable":
                    result = {
                        **result,
                        "status": status,
                        "terminal_reason": "implicit_retry_budget_exhausted",
                    }
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
            status, retry_at = _default_retry_policy(
                status="error",
                checkpoint=checkpoint,
                occurrence=occurrence,
                now=now,
                prior_attempt_count=prior_attempt_counts.get(checkpoint.key, 0),
            )
            result = {
                "status": status,
                "message": str(exc),
                **(
                    {"retry_at": retry_at.isoformat(timespec="seconds")}
                    if retry_at is not None else
                    {"terminal_reason": "implicit_retry_budget_exhausted"}
                ),
            }
            with Session(engine) as session:
                record_ranking_checkpoint_result(
                    session,
                    checkpoint,
                    status=status,
                    message=str(exc),
                    result={"error_type": type(exc).__name__},
                    retry_at=retry_at,
                    completed_at=now if status == "unavailable" else None,
                )
            results.append({"checkpoint": checkpoint.as_dict(), "result": result})

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
    runner._log("warning" if pending or unavailable else "success", message)
    return {
        # Retriable checkpoint isolation is a successful Scheduler pass; a
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
        "deferred_exchange_tails": [
            {
                **checkpoint.as_dict(),
                "reason": "exchange_tail_executor_not_production",
            }
            for checkpoint in deferred_exchange_tails
        ],
    }


def execute_ranking_lifecycle_job(runner, ctx, payload, stop_event):
    return (yield from _execute_family_job(
        runner, ctx, payload, stop_event,
        family="gameplay_rank", task_id=RANKING_LIFECYCLE_TASK_ID, label="玩法榜",
    ))


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
    "execute_magic_invasion_initialization_rnd_cell",
    "execute_beast_abyss_exchange_tail_rnd_cell",
    "execute_beast_abyss_rank_refresh_rnd_cell",
    "execute_ranking_lifecycle_job",
    "execute_resource_ranking_job",
]
