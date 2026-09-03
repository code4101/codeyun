from __future__ import annotations

"""Project Peak Race's configured child activities onto a Runtime schedule."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_export_root


PEAKRACE_OUTER_TYPE = 79
PEAKRACE_STAGE_TYPE = 80
PEAKRACE_WORSHIP_TYPE = 81
RESOURCE_RANK_TYPE = 4
_TIME_FIELDS = (
    "prepareTime",
    "startTime",
    "endTime",
    "rewardTime",
    "closePanelTime",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text and text.lstrip("-").isdigit():
            return int(text)
    return None


def _plain_name(definition: Mapping[str, Any]) -> str:
    return str(
        definition.get("name_plain") or definition.get("name") or ""
    ).strip()


def _configured_times(definition: Mapping[str, Any]) -> dict[str, str]:
    return {
        key: str(definition[key]).strip()
        for key in _TIME_FIELDS
        if str(definition.get(key) or "").strip()
    }


def _configured_weekday(definition: Mapping[str, Any]) -> int | None:
    start = str(definition.get("startTime") or "").strip()
    if not start.startswith("TC|"):
        return None
    day = start.removeprefix("TC|").partition("_")[0]
    value = _as_int(day)
    return value if value is not None and 1 <= value <= 7 else None


def _runtime_occurrences(
    activity_id: int,
    runtime_by_activity_id: Mapping[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [dict(item) for item in runtime_by_activity_id.get(activity_id, ())]


def _activity_descriptor(
    definition: Mapping[str, Any],
    *,
    role: str,
    runtime_by_activity_id: Mapping[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    activity_id = _as_int(definition.get("id"))
    if activity_id is None:
        raise ValueError("Peak Race activity definition has no numeric id")
    occurrences = _runtime_occurrences(activity_id, runtime_by_activity_id)
    return {
        "activityId": activity_id,
        "activityType": _as_int(definition.get("activityId")),
        "baseId": _as_int(definition.get("baseId")),
        "name": _plain_name(definition),
        "role": role,
        "configuredTimes": _configured_times(definition),
        "runtimeObserved": bool(occurrences),
        "runtimeOccurrences": occurrences,
    }


def _worship_for_outer(
    definitions: Mapping[int, Mapping[str, Any]],
    outer_activity_id: int,
) -> Mapping[str, Any] | None:
    for definition in definitions.values():
        if (
            _as_int(definition.get("activityId")) == PEAKRACE_WORSHIP_TYPE
            and _as_int(definition.get("modelParam")) == outer_activity_id
        ):
            return definition
    return None


def project_peakrace_schedules(
    runtime_items: Iterable[Mapping[str, Any]],
    definitions: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return configured Peak Race families represented by Runtime activity rows.

    Static descendants are never presented as observed Runtime rows.  Each
    descriptor carries ``runtimeObserved`` and the exact matching occurrences,
    so callers can distinguish configured availability from live evidence.
    """

    runtime_by_activity_id: dict[int, list[dict[str, Any]]] = {}
    for item in runtime_items:
        activity_id = _as_int(item.get("activityId"))
        if activity_id is not None:
            runtime_by_activity_id.setdefault(activity_id, []).append(dict(item))

    projected: list[dict[str, Any]] = []
    for outer_id, outer in definitions.items():
        if _as_int(outer.get("activityId")) != PEAKRACE_OUTER_TYPE:
            continue
        follow_ids = [
            value
            for raw in outer.get("follow") or ()
            if (value := _as_int(raw)) is not None
        ]
        followed = [definitions[value] for value in follow_ids if value in definitions]
        total = next(
            (
                definition
                for definition in followed
                if _as_int(definition.get("activityId")) == RESOURCE_RANK_TYPE
                and _as_int(definition.get("rewardGroup")) == outer_id
            ),
            None,
        )
        stages = [
            definition
            for definition in followed
            if _as_int(definition.get("activityId")) == PEAKRACE_STAGE_TYPE
        ]
        worship = _worship_for_outer(definitions, outer_id)
        member_ids = {outer_id, *follow_ids}
        if worship is not None:
            worship_id = _as_int(worship.get("id"))
            if worship_id is not None:
                member_ids.add(worship_id)
        observed_ids = sorted(member_ids.intersection(runtime_by_activity_id))
        if not observed_ids:
            continue

        stage_descriptors: list[dict[str, Any]] = []
        for definition in stages:
            descriptor = _activity_descriptor(
                definition,
                role="stage",
                runtime_by_activity_id=runtime_by_activity_id,
            )
            descriptor["configuredWeekday"] = _configured_weekday(definition)
            stage_descriptors.append(descriptor)
        stage_descriptors.sort(
            key=lambda item: (
                item["configuredWeekday"]
                if item["configuredWeekday"] is not None
                else 99,
                item["activityId"],
            )
        )
        for index, descriptor in enumerate(stage_descriptors, start=1):
            descriptor["stageIndex"] = index
            descriptor["roundIndex"] = (
                1 if descriptor["configuredWeekday"] in {2, 3, 4} else 2
            )

        stage_ids = [item["activityId"] for item in stage_descriptors]
        stage_weekdays = [item["configuredWeekday"] for item in stage_descriptors]
        configuration_complete = bool(
            total is not None
            and worship is not None
            and len(stage_descriptors) == 6
            and len(set(stage_ids)) == 6
            and stage_weekdays == [2, 3, 4, 5, 6, 7]
        )

        projected.append(
            {
                "outerActivityId": outer_id,
                "source": "activity_config_relationship+runtime_presence",
                "configurationComplete": configuration_complete,
                "runtimeObservedActivityIds": observed_ids,
                "outer": _activity_descriptor(
                    outer,
                    role="outer",
                    runtime_by_activity_id=runtime_by_activity_id,
                ),
                "totalRank": (
                    _activity_descriptor(
                        total,
                        role="total_rank",
                        runtime_by_activity_id=runtime_by_activity_id,
                    )
                    if total is not None
                    else None
                ),
                "worship": (
                    _activity_descriptor(
                        worship,
                        role="worship",
                        runtime_by_activity_id=runtime_by_activity_id,
                    )
                    if worship is not None
                    else None
                ),
                "stages": stage_descriptors,
            }
        )
    return projected


@lru_cache(maxsize=2)
def _load_definitions(
    path_text: str,
    _mtime_ns: int,
    _size: int,
) -> dict[int, dict[str, Any]]:
    rows = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("Activity static configuration is not a list")
    return {
        activity_id: dict(row)
        for row in rows
        if isinstance(row, Mapping)
        and (activity_id := _as_int(row.get("id"))) is not None
    }


def load_activity_definitions(
    export_root: str | Path | None = None,
) -> dict[int, dict[str, Any]]:
    root = (
        Path(export_root).expanduser().resolve()
        if export_root is not None
        else resolve_fanxiu_export_root()
    )
    path = (root / "parsed_configs" / "Activity" / "rows.json").resolve()
    stat = path.stat()
    return _load_definitions(str(path), stat.st_mtime_ns, stat.st_size)


def enrich_schedule_with_peakrace(
    schedule: Mapping[str, Any],
    *,
    export_root: str | Path | None = None,
) -> dict[str, Any]:
    """Add a non-invasive Peak Race family projection to a public schedule."""

    result = dict(schedule)
    try:
        families = project_peakrace_schedules(
            (
                item
                for item in schedule.get("items") or ()
                if isinstance(item, Mapping)
            ),
            load_activity_definitions(export_root),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update(
            {
                "peakraceScheduleComplete": False,
                "peakraceScheduleCount": 0,
                "peakraceSchedules": [],
                "peakraceScheduleReason": str(exc),
            }
        )
        return result
    result.update(
        {
            "peakraceScheduleComplete": True,
            "peakraceScheduleCount": len(families),
            "peakraceSchedules": families,
        }
    )
    return result


__all__ = [
    "enrich_schedule_with_peakrace",
    "load_activity_definitions",
    "project_peakrace_schedules",
]
