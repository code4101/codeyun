from __future__ import annotations

"""Single Scheduler ownership point for all ranking activity occurrences."""

from datetime import timedelta
import threading
from typing import Any, Iterator

from sqlmodel import Session

from backend.core.fanxiu.activity.ranking_lifecycle import (
    DAILY_RECONCILE_KIND,
    EXCHANGE_TAIL_KIND,
    MAGIC_ACTIVE_KIND,
    RANKING_LIFECYCLE_TASK_ID,
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
from backend.core.fanxiu.activity.ranking_reconcile import (
    reconcile_ranking_occurrence,
)
from backend.core.fanxiu.data_annotation.effective_time import job_now


CHECKPOINT_RETRY_DELAY = timedelta(minutes=10)


def _execute_magic_active_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    """Dispatch the first activity-specific lifecycle adapter."""

    from backend.core.fanxiu.data_annotation.tasks.magic_invasion_compound import (
        execute_magic_invasion_compound_checkpoint,
    )

    return (
        yield from execute_magic_invasion_compound_checkpoint(
            runner,
            ctx,
            payload,
            stop_event,
            occurrence=occurrence,
        )
    )


def _execute_exchange_tail_checkpoint(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
    *,
    occurrence: Any,
) -> Iterator[Any]:
    if occurrence.activity_type == "magic-invasion":
        from backend.core.fanxiu.data_annotation.tasks.magic_invasion_tail import (
            execute_magic_invasion_tail_checkpoint,
        )

        return (
            yield from execute_magic_invasion_tail_checkpoint(
                runner,
                ctx,
                payload,
                stop_event,
                occurrence=occurrence,
            )
        )
    if occurrence.activity_type == "yunmeng-trial":
        from backend.core.fanxiu.data_annotation.tasks.yunmeng_tail import (
            execute_yunmeng_tail_job,
        )

        return (yield from execute_yunmeng_tail_job(runner, ctx, payload, stop_event))
    raise RuntimeError(f"{occurrence.activity_type} 尚无兑换收尾执行适配器")


def execute_ranking_lifecycle_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: threading.Event,
) -> Iterator[Any]:
    """Reconcile every due occurrence, then persist the sole next wake-up."""

    from backend.core.fanxiu.activity.runtime_schedule import (
        read_fanxiu_activity_runtime_schedule,
    )
    from backend.db import engine

    now = job_now()
    if now.tzinfo is None:
        now = now.astimezone()
    schedule = read_fanxiu_activity_runtime_schedule(
        allow_discovery=True,
        force_refresh=True,
    )
    if not bool(schedule.get("available") and schedule.get("complete")):
        raise RuntimeError("榜单生命周期 Runtime 日程不可用或不完整")
    occurrences = discover_ranking_occurrences(schedule)
    by_instance = {item.instance_key: item for item in occurrences}
    task_id = str(ctx.get("scheduler_task_id") or RANKING_LIFECYCLE_TASK_ID)
    ensure_ranking_lifecycle_checkpoint_table(engine)
    results: list[dict[str, Any]] = []

    # Never keep a SQLite transaction open while an execution checkpoint is
    # driving the game.  Exchange/active executors collect and persist their
    # own runtime facts; an outer read transaction spanning those waits can
    # otherwise block the nested writer and self-deadlock the unified Job.
    with Session(engine) as session:
        completed = completed_ranking_checkpoint_keys(session)
        due = due_ranking_checkpoints(
            occurrences,
            now=now,
            completed_keys=completed,
        )
    for checkpoint in due:
        if stop_event.is_set():
            raise InterruptedError()
        occurrence = by_instance[checkpoint.instance_key]
        try:
            if checkpoint.checkpoint_kind == DAILY_RECONCILE_KIND:
                with Session(engine) as session:
                    result = reconcile_ranking_occurrence(
                        session,
                        occurrence,
                        captured_at=now.isoformat(timespec="seconds"),
                    )
            elif checkpoint.checkpoint_kind == EXCHANGE_TAIL_KIND:
                result = yield from _execute_exchange_tail_checkpoint(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                    occurrence=occurrence,
                )
            elif checkpoint.checkpoint_kind == MAGIC_ACTIVE_KIND:
                result = yield from _execute_magic_active_checkpoint(
                    runner,
                    ctx,
                    payload,
                    stop_event,
                    occurrence=occurrence,
                )
            else:
                raise RuntimeError(
                    f"未知榜单 checkpoint：{checkpoint.checkpoint_kind}"
                )
            status = str(result.get("status") or "completed")
            with Session(engine) as session:
                record_ranking_checkpoint_result(
                    session,
                    checkpoint,
                    status=status,
                    message=str(result.get("message") or ""),
                    result=result,
                    completed_at=now,
                )
            results.append(
                {"checkpoint": checkpoint.as_dict(), "result": result}
            )
        except (InterruptedError, KeyboardInterrupt):
            raise
        except Exception as exc:
            retry_at = now + CHECKPOINT_RETRY_DELAY
            with Session(engine) as session:
                record_ranking_checkpoint_result(
                    session,
                    checkpoint,
                    status="error",
                    message=str(exc),
                    result={"error_type": type(exc).__name__},
                    retry_at=retry_at,
                )
            results.append(
                {
                    "checkpoint": checkpoint.as_dict(),
                    "result": {
                        "status": "error",
                        "message": str(exc),
                        "retry_at": retry_at.isoformat(timespec="seconds"),
                    },
                }
            )

    with Session(engine) as session:
        completed = completed_ranking_checkpoint_keys(session)
        next_time = next_ranking_lifecycle_time(
            occurrences,
            now=now,
            completed_keys=completed,
            retry_times=ranking_checkpoint_retry_times(session),
        )

    runner._persist_scheduler_task_next_time(task_id, next_time)
    errors = [item for item in results if item["result"].get("status") == "error"]
    message = (
        f"榜单系统：处理 {len(results)} 个 checkpoint"
        f"，成功 {len(results) - len(errors)}，待重试 {len(errors)}；"
        f"下次 {next_time:%Y-%m-%d %H:%M:%S}"
    )
    runner._log("warning" if errors else "success", message)
    return {
        "result": "success",
        "message": message,
        "performed_actions": bool(results),
        "checkpoint_results": results,
    }


__all__ = ["execute_ranking_lifecycle_job"]
