from __future__ import annotations

"""Daily #66 activity occurrence synchronization business workflow.

The Runtime memory reader remains strictly read-only.  This workflow only
opens the formal #66 schedule page when the first bounded hot read reports
``not_loaded``; it then re-reads the naturally loaded model, performs a dry
run, and explicitly authorizes the persistence gate.  Unknown identities and
conflicts are audit/review facts, never guessed writes.
"""

from collections.abc import Callable, Generator, Mapping
from datetime import datetime, timedelta
from types import GeneratorType
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.daily_activity_discovery import (
    DEFAULT_TIMEZONE,
    read_daily_activity_discovery_plan,
)
from backend.core.fanxiu.activity.theme_collection import THEME_COLLECTION_TASK_ID
from backend.core.fanxiu.activity.daily_activity_sync import (
    synchronize_daily_activity_plan,
)


DAILY_ACTIVITY_LIST_SYNC_TRIGGER = (0, 0)
DAILY_ACTIVITY_LIST_SYNC_TASK_TYPE = "activity_daily_list_sync"
DAILY_ACTIVITY_LIST_SYNC_LABEL = "活动_每日清单同步"

PlanReader = Callable[..., dict[str, Any]]
PlanSynchronizer = Callable[..., dict[str, Any]]


class DailyActivityListSyncPendingResearchError(RuntimeError):
    """The formal GUI preheat path is missing or did not load its Runtime data."""


def next_daily_activity_list_sync_time(now: datetime | None = None) -> datetime:
    """Return tomorrow 00:00; recurrence belongs only to this business Job."""

    current = now or datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    tomorrow = current + timedelta(days=1)
    return tomorrow.replace(
        hour=DAILY_ACTIVITY_LIST_SYNC_TRIGGER[0],
        minute=DAILY_ACTIVITY_LIST_SYNC_TRIGGER[1],
        second=0,
        microsecond=0,
    )


def _yield_from_maybe(value: Any) -> Generator[Any, Any, Any]:
    if isinstance(value, GeneratorType):
        return (yield from value)
    return value


def _read_plan(
    reader: PlanReader,
    *,
    current: datetime,
    timezone_name: str,
) -> dict[str, Any]:
    plan = reader(
        target_date=current.astimezone(ZoneInfo(timezone_name)).date(),
        timezone_name=timezone_name,
        allow_discovery=False,
        force_refresh=False,
    )
    if not isinstance(plan, dict):
        raise RuntimeError("活动_每日清单同步：Runtime reader 返回结构无效")
    return plan


def _require_ready_plan(plan: Mapping[str, Any], *, after_preheat: bool) -> None:
    status = str(plan.get("status") or "")
    if status == "ready":
        return
    reason = str(plan.get("reason") or "Runtime 活动清单未完整加载")
    if status != "not_loaded":
        raise RuntimeError(
            "活动_每日清单同步：Runtime discovery 返回非预期状态"
            f" {status or 'unknown'}：{reason}"
        )
    stage = "#66 已自然预热后" if after_preheat else "首次热读"
    raise DailyActivityListSyncPendingResearchError(
        f"pending_research：{stage}仍无法取得完整活动清单：{reason}"
    )


def _carry_same_process_activity_observations(
    initial: Mapping[str, Any], refreshed: Mapping[str, Any]
) -> dict[str, Any]:
    """Keep #34 observations across the normal #34 -> #66 preheat transition."""

    result = dict(refreshed)
    if result.get("activity_observations"):
        return result
    observations = initial.get("activity_observations")
    if not isinstance(observations, list) or not observations:
        return result
    initial_source = dict(initial.get("source_evidence") or {}).get(
        "supplemental_activity_observation"
    )
    refreshed_runtime = dict(
        dict(refreshed.get("source_evidence") or {}).get("context") or {}
    )
    if not isinstance(initial_source, Mapping) or not initial_source.get("complete"):
        return result
    initial_runtime = dict(initial_source.get("evidence") or {})
    identity = ("pid", "process_start_ticks")
    if any(
        initial_runtime.get(key) is None
        or initial_runtime.get(key) != refreshed_runtime.get(key)
        for key in identity
    ):
        return result
    result["activity_observations"] = [
        dict(item) for item in observations if isinstance(item, Mapping)
    ]
    result["source_evidence"] = dict(refreshed.get("source_evidence") or {})
    result["source_evidence"]["supplemental_activity_observation"] = dict(
        initial_source
    )
    summary = dict(refreshed.get("summary") or {})
    summary["activity_observation_total"] = len(
        result["activity_observations"]
    )
    result["summary"] = summary
    return result


def _require_sync_result(
    result: Mapping[str, Any],
    *,
    persist: bool,
) -> None:
    allowed = (
        {"updated", "updated_with_review", "no_change", "review_required"}
        if persist
        else {"planned", "planned_with_review", "no_change", "review_required"}
    )
    status = str(result.get("status") or "")
    if status not in allowed:
        phase = "正式同步" if persist else "dry-run"
        reason = str(result.get("reason") or "同步门禁拒绝")
        raise RuntimeError(
            f"活动_每日清单同步：{phase}未通过（{status or 'unknown'}）：{reason}"
        )


def run_daily_activity_list_sync_flow(
    context: Any,
    *,
    plan_reader: PlanReader | None = None,
    synchronizer: PlanSynchronizer | None = None,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    view_timeout_seconds: float = 30.0,
) -> Generator[Any, Any, dict[str, Any]]:
    """Run one bounded observe -> preheat -> dry-run -> persist transaction.

    A failure raises before returning a ``next_time``.  Therefore the business
    Job does not advance its recurrence on incomplete Runtime data, missing
    formal #66 navigation/assets, a rejected persistence gate, or a failed
    return to #34.
    """

    from backend.core.fanxiu.data_annotation.tasks.domain_equipment import run_domain_daily_flow

    # A previous attempt may stop inside a material/recast page.  Finish that
    # observed transaction before the list-sync cleanup tries to return to the
    # world; scene navigation must never decide a candidate's keep/recast action.
    current_scene_id, _score, _frame = yield from context.current_scene(
        label="活动_每日清单同步：检查未完成镇物现场",
    )
    recovered_domain = None
    if current_scene_id in {815, 817, 818, 819}:
        recovered_domain = yield from run_domain_daily_flow(context)

    reader = plan_reader or read_daily_activity_discovery_plan
    sync = synchronizer or synchronize_daily_activity_plan
    current = now or datetime.now(ZoneInfo(timezone_name))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo(timezone_name))

    plan = _read_plan(reader, current=current, timezone_name=timezone_name)
    preheated = False
    primary_error: BaseException | None = None
    result: dict[str, Any] | None = None
    try:
        if str(plan.get("status") or "") == "not_loaded" and bool(
            plan.get("requires_ui_preheat")
        ):
            preheated = True
            try:
                yield from _yield_from_maybe(context.go_scene(66))
                yield from _yield_from_maybe(
                    context.wait_scene(
                        [66],
                        wait=float(view_timeout_seconds),
                        label="活动_每日清单同步：等待正式日程页 #66",
                    )
                )
            except (InterruptedError, GeneratorExit):
                raise
            except Exception as exc:
                raise DailyActivityListSyncPendingResearchError(
                    "pending_research：缺少可验收的 #34→#66 正式导航/资产，"
                    f"拒绝猜测入口：{type(exc).__name__}: {exc}"
                ) from exc
            initial_plan = plan
            plan = _carry_same_process_activity_observations(
                initial_plan,
                _read_plan(reader, current=current, timezone_name=timezone_name),
            )

        _require_ready_plan(plan, after_preheat=preheated)

        # Runtime discovery can take tens of seconds.  ``current`` is the
        # business-cycle anchor (target date / next_time), not a valid
        # freshness clock after that read completes.  Gate the freshly
        # captured plan against a fresh wall clock instead.
        sync_now = datetime.now(ZoneInfo(timezone_name))
        dry_run = sync(plan, persist=False, now=sync_now)
        if not isinstance(dry_run, dict):
            raise RuntimeError("活动_每日清单同步：dry-run 返回结构无效")
        _require_sync_result(dry_run, persist=False)

        # The persistence service re-reads the latest dated occurrence list
        # under its lock.  Only validated ``propose_create`` rows can be
        # appended; review rows are retained in the audit receipt.
        persisted = sync(
            plan,
            persist=True,
            now=datetime.now(ZoneInfo(timezone_name)),
        )
        if not isinstance(persisted, dict):
            raise RuntimeError("活动_每日清单同步：正式同步返回结构无效")
        _require_sync_result(persisted, persist=True)
        from backend.core.fanxiu.data_annotation.tasks.theme_collection import (
            read_theme_collection_plan,
        )

        theme_plan = read_theme_collection_plan(
            plan=plan,
            now=datetime.now(ZoneInfo(timezone_name)),
            timezone_name=timezone_name,
        )
        result = {
            "result": "success",
            "status": str(persisted.get("status") or ""),
            "preheated": preheated,
            "created_count": int(persisted.get("created_count") or 0),
            "noop_count": int(persisted.get("noop_count") or 0),
            "review_count": int(persisted.get("review_count") or 0),
            "reviews": list(persisted.get("reviews") or []),
            # The single canonical aggregation plan.  It is now the only
            # authority allowed to move a theme Job trigger.
            "theme_plan": theme_plan,
            "current_scene": 34,
        }
    except (InterruptedError, GeneratorExit):
        raise
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            yield from _yield_from_maybe(context.go_scene(34))
            yield from _yield_from_maybe(
                context.wait_scene(
                    [34],
                    wait=float(view_timeout_seconds),
                    label="活动_每日清单同步：返回世界 #34",
                )
            )
        except (InterruptedError, GeneratorExit):
            raise
        except Exception as departure_error:
            if primary_error is None:
                primary_error = RuntimeError(
                    "活动_每日清单同步：业务同步完成但未安全回到 #34："
                    f"{type(departure_error).__name__}: {departure_error}"
                )
            else:
                primary_error.add_note(
                    "且未安全回到 #34："
                    f"{type(departure_error).__name__}: {departure_error}"
                )

    if primary_error is not None:
        raise primary_error
    assert result is not None
    source = dict(plan.get("source_evidence") or {}).get("supplemental_activity_observation") or {}
    if source.get("complete") is True:
        wanxiang = [row for row in plan.get("activity_observations", [])
                    if row.get("name") == "万象宝阁" and row.get("is_schedule_occurrence") is False]
        if len(wanxiang) > 1:
            raise RuntimeError("每日清单中的万象宝阁实例不唯一")
        if wanxiang:
            from backend.core.fanxiu.instrumentation.activity_runtime import read_activity_period_runtime_snapshot
            from backend.core.fanxiu.data_annotation.tasks.wanxiang_baoge import (
                run_wanxiang_baoge_flow, wanxiang_opens_on_date,
            )
            period = read_activity_period_runtime_snapshot(int(wanxiang[0]["activity_id"]))
            evidence = source.get("evidence") or {}
            if any(period["evidence"].get(key) != evidence.get(key)
                   for key in ("pid", "process_start_ticks")):
                raise RuntimeError("万象宝阁开启时间与每日清单不属于同一进程")
            if wanxiang_opens_on_date(period, plan["target_date"], timezone_name):
                result["wanxiang_baoge"] = yield from run_wanxiang_baoge_flow(context)

    result["domain"] = (
        recovered_domain
        if recovered_domain is not None
        else (yield from run_domain_daily_flow(context))
    )

    theme_plan = result["theme_plan"]
    # The daily synchronizer only triggers the aggregation.  It writes the one
    # canonical theme-collection trigger and never a retired first-level id; an
    # incomplete theme plan schedules a bounded retry instead of clearing it.
    theme_next_time = (
        theme_plan.get("next_time") if theme_plan.get("plan_ready") else None
    ) or theme_plan.get("retry_at")
    if theme_plan.get("plan_ready") or theme_next_time:
        context.set_job_next_time(THEME_COLLECTION_TASK_ID, theme_next_time)
    context.set_next_time(
        next_daily_activity_list_sync_time(current).strftime("%Y-%m-%d %H:%M:%S")
    )
    result["message"] = (
        "活动_每日清单同步完成："
        f"新增 {result['created_count']}，已存在 {result['noop_count']}，"
        f"待复核 {result['review_count']}，"
        f"主题集计划 {'就绪' if theme_plan.get('plan_ready') else '待重试'}"
        f"（{len(theme_plan.get('occurrences') or [])} 个实例）"
    )
    if "wanxiang_baoge" in result:
        review = result["wanxiang_baoge"]
        result["message"] += "；万象宝阁：" + str(review.get("reason") or review["outcome"])
    result["message"] += "；领域处理完成并返回 #34"
    return result


class DailyActivityListSyncTaskMixin:
    """Behavior-tree integration point; registration is intentionally separate."""

    def _execute_daily_activity_list_sync_task(
        self,
        ctx: dict[str, Any],
        stop_event: Any,
        payload: dict[str, Any] | None = None,
    ) -> str:
        return self._execute_daily_task(
            ctx,
            stop_event,
            payload,
            task_type=DAILY_ACTIVITY_LIST_SYNC_TASK_TYPE,
            label=DAILY_ACTIVITY_LIST_SYNC_LABEL,
            flow=run_daily_activity_list_sync_flow,
        )


__all__ = [
    "DAILY_ACTIVITY_LIST_SYNC_LABEL",
    "DAILY_ACTIVITY_LIST_SYNC_TASK_TYPE",
    "DAILY_ACTIVITY_LIST_SYNC_TRIGGER",
    "DailyActivityListSyncPendingResearchError",
    "DailyActivityListSyncTaskMixin",
    "next_daily_activity_list_sync_time",
    "run_daily_activity_list_sync_flow",
]
