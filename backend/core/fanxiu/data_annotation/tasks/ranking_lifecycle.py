from __future__ import annotations

"""The two ranking-family Scheduler owners and their internal adapters."""

from datetime import datetime, timedelta
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import (
    DAILY_RECONCILE_KIND,
    DANDAO_REWARDS_KIND,
    EXCHANGE_TAIL_KIND,
    MAGIC_ACTIVE_KIND,
    RANKING_CAPABILITY_STATUS,
    RANKING_LIFECYCLE_TASK_ID,
    RESOURCE_FREE_GIFT_KIND,
    RESOURCE_RANKING_TASK_ID,
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
    ranking_checkpoint_retry_times,
    record_ranking_checkpoint_result,
)
from backend.core.fanxiu.activity.ranking_reconcile import reconcile_ranking_occurrence
from backend.core.fanxiu.data_annotation.effective_time import job_now


CHECKPOINT_RETRY_DELAY = timedelta(minutes=10)


def _execute_magic_active_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_compound import (
        execute_magic_invasion_compound_checkpoint,
    )
    return (yield from execute_magic_invasion_compound_checkpoint(
        runner, ctx, payload, stop_event, occurrence=occurrence
    ))


def _execute_exchange_tail_checkpoint(runner, ctx, payload, stop_event, *, occurrence):
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


def _execute_resource_checkpoint(
    runner, ctx, payload, stop_event, *, checkpoint_kind, occurrence
):
    options = dict(payload)
    options.pop("__scheduler_task_id", None)
    if checkpoint_kind == RESOURCE_FREE_GIFT_KIND:
        from backend.core.fanxiu.data_annotation.tasks.resource_rank_daily_gift import (
            run_resource_rank_daily_gift_flow,
        )
        runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        return (yield from run_resource_rank_daily_gift_flow(
            runtime,
            manage_schedule=False,
            expected_activity_type=occurrence.activity_type,
            expected_activity_id=occurrence.activity_id,
        ))
    if checkpoint_kind == DANDAO_REWARDS_KIND:
        from backend.core.fanxiu.data_annotation.tasks.dandao_task_rewards import (
            run_dandao_task_rewards_flow,
        )
        runtime = runner._fanxiu_runtime(ctx, ctx.get("asset_tree_path"), stop_event=stop_event)
        result = yield from run_dandao_task_rewards_flow(
            runtime,
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
        completed = completed_ranking_checkpoint_keys(session, family=family)
        due = due_ranking_checkpoints(occurrences, now=now, completed_keys=completed)
    xianmeng_counts: dict[str, int] = {}
    for checkpoint in due:
        if checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND:
            xianmeng_counts[checkpoint.business_date] = (
                xianmeng_counts.get(checkpoint.business_date, 0) + 1
            )
    for checkpoint in due:
        if stop_event.is_set():
            raise InterruptedError()
        occurrence = by_instance[checkpoint.instance_key]
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
                else:
                    with Session(engine) as session:
                        result = reconcile_ranking_occurrence(
                            session, occurrence, captured_at=now.isoformat(timespec="seconds")
                        )
            elif checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND:
                result = yield from _execute_exchange_tail_checkpoint(
                    runner, ctx, payload, stop_event, occurrence=occurrence
                )
            elif checkpoint.checkpoint_kind == MAGIC_ACTIVE_KIND:
                result = yield from _execute_magic_active_checkpoint(
                    runner, ctx, payload, stop_event, occurrence=occurrence
                )
            elif checkpoint.checkpoint_kind == XIANMENG_ACTIVE_KIND:
                result = yield from _execute_xianmeng_checkpoint(
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
            retry_at = now + CHECKPOINT_RETRY_DELAY
            result = {
                "status": "error",
                "message": str(exc),
                "retry_at": retry_at.isoformat(timespec="seconds"),
            }
            with Session(engine) as session:
                record_ranking_checkpoint_result(
                    session,
                    checkpoint,
                    status="error",
                    message=str(exc),
                    result={"error_type": type(exc).__name__},
                    retry_at=retry_at,
                )
            results.append({"checkpoint": checkpoint.as_dict(), "result": result})

    with Session(engine) as session:
        completed = completed_ranking_checkpoint_keys(session, family=family)
        next_time = next_ranking_lifecycle_time(
            occurrences,
            now=now,
            completed_keys=completed,
            retry_times=ranking_checkpoint_retry_times(session, family=family),
        )
    runner._persist_scheduler_task_next_time(scheduler_task_id, next_time)
    errors = [item for item in results if item["result"].get("status") == "error"]
    message = (
        f"{label}：处理 {len(results)} 个 checkpoint，成功 {len(results) - len(errors)}，"
        f"待重试 {len(errors)}；下次 {next_time:%Y-%m-%d %H:%M:%S}"
    )
    runner._log("warning" if errors else "success", message)
    return {
        "result": "success",
        "message": message,
        "performed_actions": bool(results),
        "family": family,
        "checkpoint_results": results,
    }


def execute_ranking_lifecycle_job(runner, ctx, payload, stop_event):
    return (yield from _execute_family_job(
        runner, ctx, payload, stop_event,
        family="gameplay_rank", task_id=RANKING_LIFECYCLE_TASK_ID, label="玩法榜",
    ))


def execute_resource_ranking_job(runner, ctx, payload, stop_event):
    return (yield from _execute_family_job(
        runner, ctx, payload, stop_event,
        family="resource_rank", task_id=RESOURCE_RANKING_TASK_ID, label="资源榜",
    ))


__all__ = ["execute_ranking_lifecycle_job", "execute_resource_ranking_job"]
