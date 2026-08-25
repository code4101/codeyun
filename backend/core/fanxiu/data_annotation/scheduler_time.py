from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


# Time sequence is a presentation/dispatch ordering overlay.  Persisted
# ``task["next_time"]`` always remains the original time chosen by the job.
# When several configured jobs have exactly that same original timestamp, this
# module derives an effective time by adding 0, 1, 2... minutes in configured
# order.  Never write the derived value back to the job: doing so would erase
# its business decision and make repeated projections accumulate bias.
DEFAULT_TIME_SEQUENCE: dict[str, list[str]] = {
    "00:00": [
        "legacy-daily-assistant",
        "legacy-daily-vip",
        "daily-signin",
        "weekly-activity",
        "xianshi-weekly-resources",
        "mail-selective-claim",
    ],
    "05:00": [
        "daily-boss",
        "legacy-daily-signup",
        "legacy-daily-xianshi",
        "xianqiao-trial",
        "legacy-daily-xianyuan",
        "legacy-daily-baiye",
        "legacy-daily-green-bottle-baiye",
        "daily-xuanhuang",
        "daily-weekly-dungeon",
        "weekly-hanli",
        "xianshi-weekly-resources",
    ],
    "21:30": [
        "legacy-daily-lingmai-clear",
        "legacy-daily-dongtian-clear",
        "legacy-daily-mojie-raid",
    ],
    "23:00": [
        "daily-daofa",
        "daily-xianyuan-duel",
    ],
}


def normalize_time_sequence(raw: Any) -> dict[str, list[str]]:
    """Normalize the human-configured order for each original clock."""

    if not isinstance(raw, dict):
        raw = DEFAULT_TIME_SEQUENCE
    normalized: dict[str, list[str]] = {}
    for clock, values in raw.items():
        clock_text = str(clock or "").strip()
        try:
            datetime.strptime(clock_text, "%H:%M")
        except ValueError:
            continue
        if not isinstance(values, list):
            continue
        task_ids: list[str] = []
        for value in values:
            task_id = str(value or "").strip()
            if task_id and task_id not in task_ids:
                task_ids.append(task_id)
        if task_ids:
            normalized[clock_text] = task_ids
    return normalized


def scheduler_time_bias_minutes(
    task: dict[str, Any],
    tasks: list[dict[str, Any]],
    time_sequence: dict[str, list[str]],
) -> int:
    """Return the compact bias among tasks tied on the same original timestamp.

    Merely sharing the configured ``HH:MM`` group is insufficient: bias applies
    only when the persisted absolute ``next_time`` values are genuinely tied.
    """

    next_time = parse_scheduler_time(task.get("next_time"))
    if next_time is None:
        return 0
    configured_order = time_sequence.get(next_time.strftime("%H:%M"), [])
    task_id = str(task.get("id") or "")
    if task_id not in configured_order:
        return 0
    tied_ids = {
        str(item.get("id") or "")
        for item in tasks
        if parse_scheduler_time(item.get("next_time")) == next_time
    }
    ordered_ties = [item_id for item_id in configured_order if item_id in tied_ids]
    return ordered_ties.index(task_id) if task_id in ordered_ties else 0


def effective_scheduler_time(
    task: dict[str, Any],
    tasks: list[dict[str, Any]],
    time_sequence: dict[str, list[str]],
) -> datetime | None:
    """Derive the dispatch/display time without mutating the original fact."""

    original = parse_scheduler_time(task.get("next_time"))
    if original is None:
        return None
    return original + timedelta(
        minutes=scheduler_time_bias_minutes(task, tasks, time_sequence)
    )


def scheduler_task_time_view(
    task: dict[str, Any],
    tasks: list[dict[str, Any]],
    time_sequence: dict[str, list[str]],
) -> dict[str, Any]:
    """Project a persisted job into the dispatch/display view without mutation.

    ``original_next_time`` exposes the job-owned fact; public ``next_time`` is
    the effective time after the optional sequence bias.  Dispatch and the UI
    intentionally consume the same projection so they cannot disagree.
    """

    projected = dict(task)
    original = parse_scheduler_time(task.get("next_time"))
    effective = effective_scheduler_time(task, tasks, time_sequence)
    projected["original_next_time"] = (
        original.strftime("%Y-%m-%d %H:%M:%S") if original is not None else None
    )
    projected["next_time"] = (
        effective.strftime("%Y-%m-%d %H:%M:%S") if effective is not None else None
    )
    projected["schedule_bias_minutes"] = scheduler_time_bias_minutes(
        task,
        tasks,
        time_sequence,
    )
    return projected


def scheduler_time_sequence_groups(
    tasks: list[dict[str, Any]],
    time_sequence: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Build currently relevant configured groups from original next times."""

    task_by_id = {
        str(task.get("id") or ""): task
        for task in tasks
        if str(task.get("id") or "")
    }
    groups: list[dict[str, Any]] = []
    for clock, configured_order in sorted(time_sequence.items()):
        items = [
            task_by_id[task_id]
            for task_id in configured_order
            if task_id in task_by_id
        ]
        projected_items = []
        for task in items:
            view = scheduler_task_time_view(task, tasks, time_sequence)
            projected_items.append({
                "task_id": str(task.get("id") or ""),
                "task_label": str(task.get("label") or task.get("id") or ""),
                "original_next_time": view["original_next_time"],
                "effective_next_time": view["next_time"],
                "bias_minutes": view["schedule_bias_minutes"],
            })
        groups.append({
            "key": clock,
            "original_time": clock,
            "task_ids": list(configured_order),
            "items": projected_items,
        })
    return groups


def parse_scheduler_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
