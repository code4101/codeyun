from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity.peakrace_lifecycle import (
    GUESS_POLICY_KIND,
    LIFECYCLE_CLOSE_KIND,
    OCCURRENCE_RECONCILE_KIND,
    OUTER_REWARD_KIND,
    STAGE_CLOSE_KIND,
    STAGE_OPEN_KIND,
    WORSHIP_CLOSE_KIND,
    WORSHIP_OPEN_KIND,
    checkpoints_for_peakrace,
    discover_peakrace_occurrences,
    due_peakrace_checkpoints,
    next_peakrace_lifecycle_time,
    resolve_tc_time,
)


TZ = ZoneInfo("Asia/Shanghai")


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _schedule(*, worship_runtime: bool = False):
    start = datetime(2026, 9, 1, 5, 0, tzinfo=TZ)
    end = datetime(2026, 9, 6, 22, 0, 5, tzinfo=TZ)
    close = datetime(2026, 9, 6, 23, 58, 59, tzinfo=TZ)
    stages = []
    ids = (1620100, 1620200, 1620400, 2620100, 2620200, 2620400)
    for weekday, activity_id in enumerate(ids, start=2):
        stages.append(
            {
                "activityId": activity_id,
                "name": f"stage-{weekday}",
                "roundIndex": 1 if weekday <= 4 else 2,
                "configuredTimes": {
                    "startTime": f"TC|{weekday}_5 00 5",
                    "endTime": f"TC|{weekday}_22 00 0",
                },
            }
        )
    worship_rows = []
    if worship_runtime:
        worship_rows.append(
            {
                "id": 32620002400004,
                "startTime": _ms(datetime(2026, 9, 7, 5, 0, 5, tzinfo=TZ)),
                "endTime": _ms(datetime(2026, 9, 9, 23, 59, 50, tzinfo=TZ)),
            }
        )
    return {
        "peakraceScheduleComplete": True,
        "peakraceSchedules": [
            {
                "outerActivityId": 32620001,
                "configurationComplete": True,
                "outer": {
                    "runtimeObserved": True,
                    "runtimeOccurrences": [
                        {
                            "id": 32620001400004,
                            "prepareEndTime": _ms(datetime(2026, 8, 31, 0, 0, tzinfo=TZ)),
                            "startTime": _ms(start),
                            "endTime": _ms(end),
                            "closePanelTime": _ms(close),
                        }
                    ],
                },
                "stages": stages,
                "worship": {
                    "activityId": 32620002,
                    "runtimeObserved": worship_runtime,
                    "runtimeOccurrences": worship_rows,
                },
            }
        ],
    }


def test_resolve_tc_time_uses_runtime_week_and_rejects_bad_expression():
    anchor = datetime(2026, 9, 1, 5, 0, tzinfo=TZ)
    assert resolve_tc_time("TC|2_5 00 5", week_anchor=anchor) == datetime(
        2026, 9, 1, 5, 0, 5, tzinfo=TZ
    )
    assert resolve_tc_time("ARIT|2_5 00 5", week_anchor=anchor) is None
    assert resolve_tc_time("TC|2_25 00 5", week_anchor=anchor) is None


def test_discovers_six_stage_occurrence_and_observation_only_checkpoints():
    occurrence = discover_peakrace_occurrences(_schedule())[0]
    checkpoints = checkpoints_for_peakrace(occurrence)

    assert occurrence.outer_activity_id == 32620001
    assert len(occurrence.stages) == 6
    assert occurrence.stages[0].start_at == datetime(2026, 9, 1, 5, 0, 5, tzinfo=TZ)
    assert "32620001400004" not in occurrence.instance_key
    assert len(checkpoints) == 21
    assert all(item.observation_only for item in checkpoints)
    kinds = [item.checkpoint_kind for item in checkpoints]
    assert kinds.count(OCCURRENCE_RECONCILE_KIND) == 1
    assert kinds.count(STAGE_OPEN_KIND) == 6
    assert kinds.count(GUESS_POLICY_KIND) == 6
    assert kinds.count(STAGE_CLOSE_KIND) == 6
    assert kinds.count(OUTER_REWARD_KIND) == 1
    assert kinds.count(LIFECYCLE_CLOSE_KIND) == 1
    assert WORSHIP_OPEN_KIND not in kinds


def test_worship_checkpoints_require_runtime_observation():
    occurrence = discover_peakrace_occurrences(_schedule(worship_runtime=True))[0]
    kinds = [item.checkpoint_kind for item in checkpoints_for_peakrace(occurrence)]
    assert kinds.count(WORSHIP_OPEN_KIND) == 1
    assert kinds.count(WORSHIP_CLOSE_KIND) == 1


def test_discovery_fails_closed_on_incomplete_or_ambiguous_source():
    incomplete = _schedule()
    incomplete["peakraceScheduleComplete"] = False
    assert discover_peakrace_occurrences(incomplete) == ()

    ambiguous = _schedule()
    outer = ambiguous["peakraceSchedules"][0]["outer"]
    outer["runtimeOccurrences"].append(dict(outer["runtimeOccurrences"][0]))
    assert discover_peakrace_occurrences(ambiguous) == ()


def test_due_and_next_time_respect_checkpoint_identity():
    occurrence = discover_peakrace_occurrences(_schedule())[0]
    all_rows = checkpoints_for_peakrace(occurrence)
    now = datetime(2026, 9, 1, 5, 0, 5, tzinfo=TZ)
    due = due_peakrace_checkpoints((occurrence,), now=now)
    assert [item.checkpoint_kind for item in due] == [
        OCCURRENCE_RECONCILE_KIND,
        STAGE_OPEN_KIND,
        GUESS_POLICY_KIND,
    ]

    completed = {item.key for item in due}
    assert due_peakrace_checkpoints((occurrence,), now=now, completed_keys=completed) == ()
    assert next_peakrace_lifecycle_time(
        (occurrence,), now=now, completed_keys=completed
    ) == datetime(2026, 9, 1, 22, 0, tzinfo=TZ)
    assert len({item.key for item in all_rows}) == len(all_rows)


def test_instance_key_survives_runtime_row_refresh():
    first = discover_peakrace_occurrences(_schedule())[0]
    refreshed_schedule = _schedule()
    refreshed_schedule["peakraceSchedules"][0]["outer"]["runtimeOccurrences"][0]["id"] = 999
    refreshed = discover_peakrace_occurrences(refreshed_schedule)[0]

    assert refreshed.runtime_id != first.runtime_id
    assert refreshed.instance_key == first.instance_key
