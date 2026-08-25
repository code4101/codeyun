from __future__ import annotations

"""Read-only projection of the loaded #66 schedule into a daily sync plan.

This module deliberately does not persist anything.  The Runtime snapshot is
the authority for occurrences; the static activity catalog only resolves an
``activityId`` to a wiki card, while the small inventory activity list stores
user-facing dated occurrences.  Keeping those identities separate prevents a
new Runtime row from silently rewriting either source.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from backend.core.fanxiu.catalog.inventory import load_activity_list
from backend.core.fanxiu.instrumentation.activity_runtime import (
    _load_activity_definitions,
    read_worldline_activity_runtime_snapshot,
)
from backend.core.fanxiu.instrumentation.revenue_activity_observation import (
    read_revenue_activity_observation_snapshot,
)


DEFAULT_TIMEZONE = "Asia/Shanghai"


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value or "").strip()


def _target_date(value: date | str | None, timezone_name: str) -> date:
    if value is None:
        return datetime.now(ZoneInfo(timezone_name)).date()
    if isinstance(value, datetime):
        return value.astimezone(ZoneInfo(timezone_name)).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _epoch_ms_datetime(value: Any, timezone_name: str) -> datetime | None:
    millis = _as_int(value)
    if millis is None or millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, ZoneInfo(timezone_name))


def _occurrence_key(item: Mapping[str, Any]) -> str:
    return "|".join(
        str(item.get(key) if item.get(key) is not None else "")
        for key in ("activityId", "startTime", "endTime", "serverCount")
    )


def _existing_key(item: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(item.get("name") or "").strip(),
        _as_int(item.get("cross_count")) or 0,
        _date_text(item.get("start_date")),
        _date_text(item.get("end_date")),
    )


def _group_runtime_items(
    items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Coalesce duplicate VO projections without losing Runtime identities."""

    grouped: dict[str, dict[str, Any]] = {}
    for source in items:
        item = dict(source)
        key = _occurrence_key(item)
        if not key.strip("|"):
            # The strict Runtime reader normally rejects this.  Preserve the
            # row as a unique incomplete candidate if a fixture/adapter passes
            # it through, rather than collapsing unrelated unknown entries.
            key = f"incomplete:{len(grouped) + 1}"
        current = grouped.get(key)
        runtime_id = _as_int(item.get("id"))
        if current is None:
            item["runtime_ids"] = [runtime_id] if runtime_id is not None else []
            grouped[key] = item
            continue
        if runtime_id is not None and runtime_id not in current["runtime_ids"]:
            current["runtime_ids"].append(runtime_id)
        # Conflicting names for one exact occurrence identity are evidence of
        # an incoherent projection, not a reason to choose one arbitrarily.
        names = {
            str(value or "").strip()
            for value in (current.get("name"), item.get("name"))
            if str(value or "").strip()
        }
        if len(names) > 1:
            current["identityConflict"] = sorted(names)
    return list(grouped.values())


def _normalize_occurrence(
    item: Mapping[str, Any],
    *,
    timezone_name: str,
    known_catalog_ids: set[int],
) -> dict[str, Any]:
    activity_id = _as_int(item.get("activityId"))
    activity_type = _as_int(item.get("activityType"))
    start = _epoch_ms_datetime(item.get("startTime"), timezone_name)
    end = _epoch_ms_datetime(item.get("endTime"), timezone_name)
    prepare = _epoch_ms_datetime(item.get("prepareEndTime"), timezone_name)
    close = _epoch_ms_datetime(item.get("closePanelTime"), timezone_name)
    name = str(item.get("name") or "").strip()
    server_count = _as_int(item.get("serverCount"))
    catalog_known = activity_id is not None and activity_id in known_catalog_ids
    identity_complete = bool(
        item.get("identityComplete")
        and activity_id is not None
        and activity_type is not None
        and start is not None
        and end is not None
        and name
        and catalog_known
        and not item.get("identityConflict")
    )
    return {
        "key": _occurrence_key(item),
        "runtime_ids": list(item.get("runtime_ids") or []),
        "activity_id": activity_id,
        "activity_type": activity_type,
        "base_id": _as_int(item.get("baseId")),
        "schedule_id": _as_int(item.get("scheduleId")) or 0,
        "state": _as_int(item.get("state")),
        "name": name,
        "display_name": name or (
            f"未知活动 {activity_id}" if activity_id is not None else "未知活动"
        ),
        "cross_count": server_count or 0,
        "prepare_at": prepare.isoformat(timespec="seconds") if prepare else "",
        "start_at": start.isoformat(timespec="seconds") if start else "",
        "end_at": end.isoformat(timespec="seconds") if end else "",
        "close_panel_at": close.isoformat(timespec="seconds") if close else "",
        "start_date": start.date().isoformat() if start else "",
        "end_date": end.date().isoformat() if end else "",
        "catalog_card_id": str(activity_id) if catalog_known else "",
        "catalog_status": "known" if catalog_known else "missing",
        "identity_complete": identity_complete,
        "identity_conflict": list(item.get("identityConflict") or []),
        "raw": dict(item),
    }


def _on_target_day(
    occurrence: Mapping[str, Any],
    *,
    target: date,
    timezone_name: str,
) -> tuple[bool, str]:
    timezone = ZoneInfo(timezone_name)
    day_start = datetime.combine(target, time.min, timezone)
    day_end = day_start + timedelta(days=1)
    start_text = str(occurrence.get("start_at") or "")
    end_text = str(occurrence.get("end_at") or "")
    close_text = str(occurrence.get("close_panel_at") or "")
    if not start_text or not end_text:
        return False, "invalid_period"
    start = datetime.fromisoformat(start_text)
    end = datetime.fromisoformat(end_text)
    if start < day_end and end > day_start:
        if start.date() == target:
            return True, "starts_today"
        return True, "continues_today"
    if close_text:
        close = datetime.fromisoformat(close_text)
        if end <= day_start < close:
            return True, "claim_grace_today"
    return False, "outside_day"


def build_daily_activity_sync_plan(
    runtime_snapshot: Mapping[str, Any],
    existing_occurrences: Sequence[Mapping[str, Any]],
    *,
    activity_observation_snapshot: Mapping[str, Any] | None = None,
    known_catalog_ids: Iterable[int] = (),
    target_date: date | str | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Build a deterministic plan; never write catalog or occurrence data."""

    target = _target_date(target_date, timezone_name)
    activity_observations = []
    if (
        activity_observation_snapshot is not None
        and activity_observation_snapshot.get("complete")
    ):
        for raw in activity_observation_snapshot.get("items") or []:
            if not isinstance(raw, Mapping):
                continue
            # Supplemental sources never acquire #66 schedule semantics merely
            # by participating in the daily discovery transaction.
            observation = {
                key: value
                for key, value in dict(raw).items()
                if key
                not in {
                    "schedule_id",
                    "scheduleId",
                    "start_at",
                    "startTime",
                    "end_at",
                    "endTime",
                    "prepare_at",
                    "prepareEndTime",
                    "close_panel_at",
                    "closePanelTime",
                }
            }
            observation["is_schedule_occurrence"] = False
            activity_observations.append(observation)
    source_evidence = {
        "count": _as_int(runtime_snapshot.get("count")),
        "declared_count": _as_int(runtime_snapshot.get("declared_count")),
        "resolved_identity_count": _as_int(
            runtime_snapshot.get("resolved_identity_count")
        ),
        "unresolved_identity_count": _as_int(
            runtime_snapshot.get("unresolved_identity_count")
        ),
        "runtime": dict(runtime_snapshot.get("evidence") or {}),
    }
    if activity_observation_snapshot is not None:
        source_evidence["supplemental_activity_observation"] = {
            "complete": bool(activity_observation_snapshot.get("complete")),
            "count": len(activity_observations),
            "source_kind": str(
                activity_observation_snapshot.get("source_kind") or ""
            ),
            "captured_at": str(
                activity_observation_snapshot.get("captured_at") or ""
            ),
            "reason": str(activity_observation_snapshot.get("reason") or ""),
            "evidence": dict(activity_observation_snapshot.get("evidence") or {}),
        }
    base = {
        "target_date": target.isoformat(),
        "timezone": timezone_name,
        "source_kind": str(runtime_snapshot.get("source_kind") or ""),
        "captured_at": str(runtime_snapshot.get("captured_at") or ""),
        "source_evidence": source_evidence,
        "write_authorized": False,
        "occurrences": [],
        "activity_observations": activity_observations,
        "operations": [],
    }
    if not runtime_snapshot.get("complete"):
        return {
            **base,
            "status": "not_loaded",
            "reason": str(runtime_snapshot.get("reason") or "Runtime 活动清单未加载"),
            "requires_ui_preheat": True,
            "summary": {
                "total": 0,
                **(
                    {"activity_observation_total": len(activity_observations)}
                    if activity_observation_snapshot is not None
                    else {}
                ),
            },
        }

    catalog_ids = {
        value
        for raw in known_catalog_ids
        if (value := _as_int(raw)) is not None and value > 0
    }
    existing_by_key = {
        _existing_key(item): dict(item) for item in existing_occurrences
    }
    existing_by_name_dates: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in existing_occurrences:
        key = _existing_key(item)
        existing_by_name_dates.setdefault((key[0], key[2], key[3]), []).append(
            dict(item)
        )

    occurrences: list[dict[str, Any]] = []
    operations: list[dict[str, Any]] = []
    for grouped in _group_runtime_items(runtime_snapshot.get("items") or []):
        occurrence = _normalize_occurrence(
            grouped,
            timezone_name=timezone_name,
            known_catalog_ids=catalog_ids,
        )
        included, day_relation = _on_target_day(
            occurrence,
            target=target,
            timezone_name=timezone_name,
        )
        # Discovery is the lossless fact layer.  Target-day membership is only
        # an annotation for downstream projections; it must never decide which
        # Runtime rows survive the sync plan.
        occurrence["on_target_day"] = included
        occurrence["day_relation"] = day_relation
        occurrence["ends_today"] = (
            occurrence["end_date"] == target.isoformat()
            and day_relation in {"starts_today", "continues_today"}
        )
        occurrences.append(occurrence)
        if not included:
            continue
        key = (
            occurrence["name"],
            occurrence["cross_count"],
            occurrence["start_date"],
            occurrence["end_date"],
        )
        exact = existing_by_key.get(key)
        same_name_dates = existing_by_name_dates.get(
            (key[0], key[2], key[3]), []
        )
        if not occurrence["identity_complete"]:
            action = "review_unknown_identity"
            reason = "Runtime 身份未能与静态活动图鉴唯一对齐"
        elif exact is not None:
            action = "noop"
            reason = "活动实例已存在且名称、跨数、日期完全一致"
        elif occurrence["schedule_id"] <= 0:
            action = "observe_only"
            reason = "世界线活动未声明 #66 scheduleId，不自动写入轮换活动清单"
        elif same_name_dates:
            action = "review_scope_conflict"
            reason = "同名同期活动已存在，但跨数不同"
        else:
            action = "propose_create"
            reason = "图鉴身份完整，但本期活动实例尚未登记"
        proposed = None
        if action == "propose_create":
            proposed = {
                "id": (
                    f"runtime-{occurrence['activity_id']}-"
                    f"{str(grouped.get('startTime') or '')}-"
                    f"{occurrence['cross_count']}"
                ),
                "name": occurrence["name"],
                "cross_count": occurrence["cross_count"],
                "start_date": occurrence["start_date"],
                "end_date": occurrence["end_date"],
            }
        operations.append(
            {
                "action": action,
                "reason": reason,
                "occurrence": occurrence,
                "existing": exact,
                "conflicting_existing": same_name_dates if not exact else [],
                "proposed_occurrence": proposed,
            }
        )

    action_counts = Counter(item["action"] for item in operations)
    return {
        **base,
        "status": "ready",
        "reason": "已从当前完整 Runtime 清单生成只读同步计划",
        "requires_ui_preheat": False,
        "occurrences": occurrences,
        "operations": operations,
        "summary": {
            "total": len(occurrences),
            **(
                {"activity_observation_total": len(activity_observations)}
                if activity_observation_snapshot is not None
                else {}
            ),
            "projected_total": len(operations),
            "starts_today": sum(
                item["day_relation"] == "starts_today"
                for item in occurrences
            ),
            "continues_today": sum(
                item["day_relation"] == "continues_today"
                for item in occurrences
            ),
            "claim_grace_today": sum(
                item["day_relation"] == "claim_grace_today"
                for item in occurrences
            ),
            "outside_day": sum(
                item["day_relation"] == "outside_day"
                for item in occurrences
            ),
            "ends_today": sum(
                bool(item["ends_today"])
                for item in occurrences
            ),
            **dict(sorted(action_counts.items())),
        },
    }


def read_daily_activity_discovery_plan(
    *,
    target_date: date | str | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    allow_discovery: bool = False,
    force_refresh: bool = False,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Read current Runtime and return a non-persisting daily plan.

    With the default ``allow_discovery=False`` this is a bounded hot read.  A
    NotLoaded result asks the future business Job to navigate to #66 through
    its normal GUI path and then retry; this function never initializes a game
    Manager or falls back to historical snapshots.
    """

    runtime_snapshot = read_worldline_activity_runtime_snapshot(
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
        export_root=export_root,
    )
    activity_observation_snapshot = read_revenue_activity_observation_snapshot(
        force_refresh=force_refresh
    )
    if not runtime_snapshot.get("complete"):
        # Keep the hot NotLoaded path cheap and side-effect free.  There is no
        # useful diff to compute until the current Runtime list exists.
        return build_daily_activity_sync_plan(
            runtime_snapshot,
            (),
            activity_observation_snapshot=activity_observation_snapshot,
            target_date=target_date,
            timezone_name=timezone_name,
        )
    definitions = _load_activity_definitions(export_root)
    return build_daily_activity_sync_plan(
        runtime_snapshot,
        load_activity_list(),
        activity_observation_snapshot=activity_observation_snapshot,
        known_catalog_ids=definitions,
        target_date=target_date,
        timezone_name=timezone_name,
    )


__all__ = [
    "DEFAULT_TIMEZONE",
    "build_daily_activity_sync_plan",
    "read_daily_activity_discovery_plan",
]
