from __future__ import annotations

"""Canonical ``theme-collection`` Job: one read-only plan and one serial loop.

The daily activity reader owns Runtime facts.  This module is the execution
projection of the single Canonical Job: it asks for a plan, runs the due member
components serially, records a stage completion only after a real success, and
writes exactly one parent ``next_time``.  It never writes a retired first-level
Job id and never guesses an activity from a weekday.

Reality gates existence.  The Xianyuan Banquet mother occurrence comes from the
DoupoParty/ActivityMgr activation dictionary; every other known theme needs an
independently authoritative period (a complete ``#66`` occurrence or an exact
``Revenue`` ActivityVO read).  A theme without authority becomes a diagnostic,
not a scheduled run.
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import DEFAULT_TIMEZONE
from backend.core.fanxiu.activity.theme_collection import (
    PRODUCTION_THEME_STAGE_KINDS,
    HOLY_WOOD_TAIL_KIND,
    THEME_COLLECTION_LABEL,
    THEME_COLLECTION_TASK_ID,
    THEME_COLLECTION_TASK_TYPE,
    XIANYUAN_BANQUET_BASE_ID,
    project_theme_collection,
    theme_member_for_row,
)
from backend.core.fanxiu.activity.theme_collection_store import (
    completed_theme_stage_keys,
    persist_theme_stage_completion,
)

THEME_COLLECTION_RETRY_MINUTES = 5


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _default_plan_reader(**kwargs: Any) -> dict[str, Any]:
    from backend.core.fanxiu.activity.daily_activity_discovery import (
        read_daily_activity_discovery_plan,
    )

    return read_daily_activity_discovery_plan(**kwargs)


def _default_xianyuan_reader(
    *, allow_discovery: bool = False, force_refresh: bool = False
) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.xianyuan_banquet import (
        read_xianyuan_banquet_runtime,
    )

    return read_xianyuan_banquet_runtime(
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
    )


def _default_period_reader(activity_id: int) -> dict[str, Any]:
    from backend.core.fanxiu.instrumentation.activity_runtime import (
        read_activity_period_runtime_snapshot,
    )

    return read_activity_period_runtime_snapshot(activity_id)


def _default_completion_reader() -> set[tuple[str, str, str, str]]:
    from sqlmodel import Session

    from backend.db import engine

    with Session(engine) as session:
        return completed_theme_stage_keys(session)


def _persist_stage_completion(stage: Mapping[str, Any], *, completed_at: datetime) -> None:
    from sqlmodel import Session

    from backend.db import engine

    with Session(engine) as session:
        persist_theme_stage_completion(
            session,
            instance_key=str(stage["instance_key"]),
            member_id=str(stage["member_id"]),
            stage=str(stage["stage"]),
            stage_kind=str(stage["kind"]),
            business_date=str(stage["business_date"]),
            completed_at=completed_at,
        )


def _activation_occurrences(xianyuan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Mother activity rows only; the base-118000 page owns the instance."""

    rows: list[dict[str, Any]] = []
    for raw in xianyuan.get("occurrences") or []:
        if not isinstance(raw, Mapping):
            continue
        if _as_int(raw.get("base_id")) != XIANYUAN_BANQUET_BASE_ID:
            continue
        rows.append(dict(raw))
    return rows


def _period_authority_occurrences(
    plan: Mapping[str, Any],
    *,
    period_reader: Callable[[int], Mapping[str, Any]],
    timezone_name: str,
) -> list[dict[str, Any]]:
    """Exact Revenue/ActivityVO periods for known themes observed in the list.

    A visible Revenue row proves presence, not an end date.  The period read is
    the separate authority; a missing/ambiguous period contributes no occurrence
    so the member is reported as a diagnostic instead of being guessed.
    """

    tz = ZoneInfo(timezone_name)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in plan.get("occurrences") or []:
        if not isinstance(raw, Mapping):
            continue
        activity_id = _as_int(raw.get("activity_id"))
        if activity_id is not None and raw.get("identity_complete") is True:
            # A complete #66 occurrence is already authoritative; the Revenue
            # period must not create a second instance for the same activity.
            seen.add(activity_id)
    for raw in plan.get("activity_observations") or []:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("is_schedule_occurrence") is not False:
            continue
        name = str(raw.get("name") or "").strip()
        activity_id = _as_int(raw.get("activity_id"))
        if not name or activity_id is None or activity_id <= 0 or activity_id in seen:
            continue
        if theme_member_for_row({"name": name, "activity_id": activity_id}) is None:
            continue
        try:
            period = period_reader(activity_id)
        except Exception:
            continue
        if not isinstance(period, Mapping) or period.get("complete") is not True:
            continue
        start_ms = _as_int(period.get("start_time_ms"))
        end_ms = _as_int(period.get("end_time_ms"))
        if not start_ms or not end_ms or end_ms <= start_ms:
            continue
        seen.add(activity_id)
        rows.append(
            {
                "name": name,
                "activity_id": activity_id,
                "source_kind": "revenue_activity_period_runtime_memory",
                "start_at": datetime.fromtimestamp(start_ms / 1000, tz).isoformat(
                    timespec="seconds"
                ),
                "end_at": datetime.fromtimestamp(end_ms / 1000, tz).isoformat(
                    timespec="seconds"
                ),
            }
        )
    return rows


def read_theme_collection_plan(
    *,
    plan: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    target_date: date | str | None = None,
    allow_discovery: bool = False,
    force_refresh: bool = False,
    plan_reader: Callable[..., Mapping[str, Any]] | None = None,
    xianyuan_reader: Callable[..., Mapping[str, Any]] | None = None,
    period_reader: Callable[[int], Mapping[str, Any]] | None = None,
    completion_reader: Callable[[], Iterable[tuple[str, str, str, str]]] | None = None,
) -> dict[str, Any]:
    """Read one coherent theme plan without writing any state.

    ``plan_ready`` is False when the daily reader did not return a complete
    Runtime plan and no occurrence was independently certified.  Callers must
    then leave ``next_time`` untouched and only schedule a bounded retry; an
    incomplete fact read may never clear an existing trigger.
    """

    tz = ZoneInfo(timezone_name)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        raise ValueError("主题集计划时钟必须带时区")
    current = current.astimezone(tz)

    reader = plan_reader or _default_plan_reader
    resolved_plan: Mapping[str, Any]
    if plan is not None:
        resolved_plan = plan
    else:
        resolved_plan = reader(
            target_date=target_date,
            timezone_name=timezone_name,
            allow_discovery=allow_discovery,
            force_refresh=force_refresh,
        )
    if not isinstance(resolved_plan, Mapping):
        raise ValueError("主题集计划读取返回结构无效")

    xianyuan = (xianyuan_reader or _default_xianyuan_reader)(
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
    )
    activation = _activation_occurrences(
        xianyuan if isinstance(xianyuan, Mapping) else {}
    )
    authority = _period_authority_occurrences(
        resolved_plan,
        period_reader=period_reader or _default_period_reader,
        timezone_name=timezone_name,
    )
    reader_for_completion = completion_reader or _default_completion_reader
    completed = set(reader_for_completion())
    # 事实读取可能跨过 21/22 点或午夜；生产模式按读取完成时刻判定。
    # 显式 now 保留为纯计划查询/测试所要求的业务时刻。
    if now is None:
        current = datetime.now(tz)

    projection = project_theme_collection(
        resolved_plan,
        completed_stage_keys=completed,
        activation_occurrences=activation,
        authority_occurrences=authority,
        now=current,
        timezone_name=timezone_name,
        production_kinds=PRODUCTION_THEME_STAGE_KINDS,
    )

    occurrences = projection.get("occurrences") or []
    supplemental = (resolved_plan.get("source_evidence") or {}).get("supplemental_activity_observation") or {}
    sources_complete = (
        resolved_plan.get("status") == "ready"
        and supplemental.get("complete") is True
        and isinstance(xianyuan, Mapping) and xianyuan.get("available") is True
    )
    # Naturally unloaded banquet details do not invalidate ActivityMgr periods.
    # Conversely, an empty unavailable read must never prove activity absence.
    plan_ready = bool(occurrences) or (sources_complete and not projection.get("diagnostics"))
    next_time = projection["desired_next_times"].get(THEME_COLLECTION_TASK_ID)
    retry_at = (
        (current + timedelta(minutes=THEME_COLLECTION_RETRY_MINUTES)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if not plan_ready or projection.get("diagnostics")
        else None
    )
    return {
        "status": projection["status"],
        "plan_ready": plan_ready,
        "next_time": next_time if plan_ready else None,
        "retry_at": retry_at,
        "due_stages": list(projection.get("due_stages") or []),
        "occurrences": list(occurrences),
        "diagnostics": list(projection.get("diagnostics") or []),
        "deferred_stages": list(projection.get("deferred_stages") or []),
        "captured_at": current.isoformat(timespec="seconds"),
    }


def _stage_succeeded(result: Any) -> bool:
    if isinstance(result, Mapping):
        if str(result.get("result") or "") == "success":
            return True
        if str(result.get("status") or "") in {"round_complete", "success"}:
            return True
    return str(result or "") == "success"


def _reschedule(runner: Any, next_time: str | None) -> None:
    # None is an explicit dormant decision after a complete, empty plan.
    runner._persist_scheduler_task_next_time(THEME_COLLECTION_TASK_ID, next_time)


def _execute_member(
    runner: Any,
    context: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
    stage: Mapping[str, Any],
):
    """Dispatch one due stage to its already-authorized member component.

    The aggregation call always runs a component with scheduling disabled, so a
    retired first-level Job id can never be written or resurrected.
    """

    member = str(stage.get("member_id") or "")
    kind = str(stage.get("kind") or "")

    if member == "xianyuan-banquet":
        if kind.startswith("holy_wood_prayer"):
            from backend.core.fanxiu.data_annotation.tasks.holy_wood_prayer import (
                execute_holy_wood_prayer_task,
            )

            return (yield from execute_holy_wood_prayer_task(
                runner, ctx, {**payload, "remainder_mode":
                              "single" if kind == HOLY_WOOD_TAIL_KIND else "defer"}, stop_event
            ))
        if kind.startswith("garden_banquet"):
            from backend.core.fanxiu.data_annotation.tasks.xianyan_cycle import (
                execute_xianyan_cycle,
            )

            return (yield from execute_xianyan_cycle(runner, context, stop_event))
        if kind == "visit_xianzun_2200":
            from backend.core.fanxiu.data_annotation.tasks.theme_npc_gift import execute_theme_npc_gift

            return (yield from execute_theme_npc_gift(context))
        raise RuntimeError(f"主题集未接入的仙园游宴阶段：{kind}")

    if member == "penglai-xianzang":
        from backend.core.fanxiu.data_annotation.tasks.penglai_xianzang_jobs import (
            execute_xianzang_config_job,
            execute_xianzang_lottery_job,
        )

        job_payload = {**payload, "schedule": False}
        if kind.startswith("xianzang_config"):
            return (yield from execute_xianzang_config_job(
                runner, ctx, job_payload, stop_event
            ))
        if kind.startswith("xianzang_lottery"):
            return (yield from execute_xianzang_lottery_job(
                runner, ctx, job_payload, stop_event
            ))
        raise RuntimeError(f"主题集未接入的蓬莱仙藏阶段：{kind}")

    if member == "kunlun-secret":
        from backend.core.fanxiu.data_annotation.tasks.kunlun_secret_jobs import (
            execute_kunlun_config_job,
            execute_kunlun_lottery_job,
        )

        job_payload = {**payload, "schedule": False}
        if kind.startswith("kunlun_config"):
            return (yield from execute_kunlun_config_job(
                runner, ctx, job_payload, stop_event
            ))
        if kind.startswith("kunlun_lottery"):
            return (yield from execute_kunlun_lottery_job(
                runner, ctx, job_payload, stop_event
            ))
        raise RuntimeError(f"主题集未接入的昆仑秘藏阶段：{kind}")

    if member == "lingxiao-xianhui":
        from backend.core.fanxiu.data_annotation.tasks.lingxiao_xianhui import (
            execute_lingxiao_xianhui_job,
        )

        return (yield from execute_lingxiao_xianhui_job(
            runner, ctx, {**payload, "schedule": False}, stop_event
        ))

    if member == "wanbao-zhenbao":
        from backend.core.fanxiu.data_annotation.tasks.wanbao_zhenbao_job import (
            execute_wanbao_zhenbao_job,
        )

        return (yield from execute_wanbao_zhenbao_job(
            runner, ctx, {**payload, "schedule": False}, stop_event
        ))

    raise RuntimeError(f"主题集未接入的成员：{member}（{kind}）")


def execute_theme_collection_job(
    runner: Any,
    ctx: dict[str, Any],
    payload: dict[str, Any],
    stop_event: Any,
):
    """Run every due member stage serially, then write the one parent trigger.

    A member that returns ``pending`` (or otherwise does not prove success) is
    never recorded as a completion; the Job reschedules a bounded retry and
    returns without advancing the business cursor for that stage.  Any raised
    member failure propagates and stops the loop.
    """

    tz = ZoneInfo(DEFAULT_TIMEZONE)
    context = runner._behavior_tree_context(
        ctx, ctx.get("asset_tree_path"), stop_event=stop_event
    )
    plan = read_theme_collection_plan()

    if not plan["plan_ready"]:
        _reschedule(runner, plan.get("retry_at"))
        yield from context.go_scene(34)
        return {
            "result": "pending",
            "message": "主题集计划不完整，保留父任务并安排有界重试",
            "status": plan["status"],
            "final_scene": 34,
        }

    executed: list[dict[str, Any]] = []
    for index, stage in enumerate(plan["due_stages"]):
        # A previous component may run across a window boundary. Recheck the
        # current period and slot before dispatch, never replay an old intent.
        # 第一项直接消费刚读完的事实；后续项才复查上一组件造成的时间变化。
        fresh = plan if index == 0 else read_theme_collection_plan()
        identity = tuple(stage[k] for k in ("instance_key", "member_id", "kind", "business_date"))
        if not any(tuple(row[k] for k in ("instance_key", "member_id", "kind", "business_date")) == identity
                   for row in fresh["due_stages"]):
            continue
        result = yield from _execute_member(
            runner, context, ctx, payload, stop_event, stage
        )
        if not _stage_succeeded(result):
            retry_at = (datetime.now(tz) + timedelta(minutes=THEME_COLLECTION_RETRY_MINUTES)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            _reschedule(runner, retry_at)
            yield from context.go_scene(34)
            return {
                "result": "pending",
                "message": (
                    "主题集阶段未证明完成，不记录完成并安排重试："
                    f"{stage.get('member_id')}/{stage.get('kind')}"
                ),
                "stage": dict(stage),
                "final_scene": 34,
            }
        _persist_stage_completion(stage, completed_at=datetime.now(tz))
        executed.append(dict(stage))

    refreshed = read_theme_collection_plan()
    candidates = [value for value in (refreshed.get("next_time"), refreshed.get("retry_at")) if value]
    next_time = min(candidates) if candidates else None
    _reschedule(runner, next_time)
    yield from context.go_scene(34)
    return {
        "result": "success",
        "message": f"主题集本轮完成 {len(executed)} 个阶段，下次 {next_time or '休眠，等待活动清单'}",
        "executed_stages": executed,
        "final_scene": 34,
    }


__all__ = [
    "THEME_COLLECTION_LABEL",
    "THEME_COLLECTION_RETRY_MINUTES",
    "THEME_COLLECTION_TASK_ID",
    "THEME_COLLECTION_TASK_TYPE",
    "execute_theme_collection_job",
    "read_theme_collection_plan",
]
