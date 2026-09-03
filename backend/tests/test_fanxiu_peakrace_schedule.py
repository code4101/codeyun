from __future__ import annotations

from backend.core.fanxiu.activity import peakrace_schedule, runtime_schedule


def _definitions() -> dict[int, dict]:
    definitions = {
        32620001: {
            "id": 32620001,
            "activityId": 79,
            "name_plain": "巅峰赛",
            "baseId": 170000,
            "follow": [
                623001,
                1620100,
                1620200,
                1620400,
                2620100,
                2620200,
                2620400,
            ],
        },
        623001: {
            "id": 623001,
            "activityId": 4,
            "name_plain": "总榜",
            "baseId": 170001,
            "rewardGroup": 32620001,
            "startTime": "TC|2_5 00 5",
        },
        32620002: {
            "id": 32620002,
            "activityId": 81,
            "name_plain": "仙道膜拜",
            "baseId": 170002,
            "modelParam": "32620001",
            "startTime": "ARIT|1_5 00 05",
        },
    }
    for index, activity_id in enumerate(
        [1620100, 1620200, 1620400, 2620100, 2620200, 2620400],
        start=2,
    ):
        definitions[activity_id] = {
            "id": activity_id,
            "activityId": 80,
            "name_plain": f"子榜 {index}",
            "baseId": 43000 + (index % 3),
            "startTime": f"TC|{index}_5 00 5",
            "endTime": f"TC|{index}_22 00 0",
        }
    return definitions


def test_project_discovers_total_six_stages_and_worship_from_outer() -> None:
    runtime = [
        {
            "id": 32620001400001,
            "activityId": 32620001,
            "activityType": 79,
            "state": 2,
            "startTime": 1788238805000,
            "endTime": 1788712805000,
        }
    ]

    families = peakrace_schedule.project_peakrace_schedules(
        runtime,
        _definitions(),
    )

    assert len(families) == 1
    family = families[0]
    assert family["outerActivityId"] == 32620001
    assert family["configurationComplete"] is True
    assert family["runtimeObservedActivityIds"] == [32620001]
    assert family["totalRank"]["activityId"] == 623001
    assert family["worship"]["activityId"] == 32620002
    assert [item["activityId"] for item in family["stages"]] == [
        1620100,
        1620200,
        1620400,
        2620100,
        2620200,
        2620400,
    ]
    assert [item["roundIndex"] for item in family["stages"]] == [1, 1, 1, 2, 2, 2]
    assert [item["configuredWeekday"] for item in family["stages"]] == [
        2,
        3,
        4,
        5,
        6,
        7,
    ]
    assert family["outer"]["runtimeObserved"] is True
    assert family["totalRank"]["runtimeObserved"] is False
    assert family["stages"][0]["configuredTimes"] == {
        "startTime": "TC|2_5 00 5",
        "endTime": "TC|2_22 00 0",
    }


def test_project_activates_family_when_only_child_is_runtime_observed() -> None:
    runtime = [
        {
            "id": 1620100400001,
            "activityId": 1620100,
            "activityType": 80,
            "state": 2,
            "startTime": 1788238805000,
            "endTime": 1788300000000,
        }
    ]

    family = peakrace_schedule.project_peakrace_schedules(
        runtime,
        _definitions(),
    )[0]

    assert family["runtimeObservedActivityIds"] == [1620100]
    assert family["outer"]["runtimeObserved"] is False
    assert family["stages"][0]["runtimeObserved"] is True
    assert family["stages"][0]["runtimeOccurrences"] == runtime


def test_project_does_not_publish_inactive_static_families() -> None:
    assert peakrace_schedule.project_peakrace_schedules([], _definitions()) == []


def test_project_sorts_follow_rows_by_weekday_and_rejects_duplicate_stage() -> None:
    definitions = _definitions()
    outer = definitions[32620001]
    outer["follow"] = [
        623001,
        1620400,
        1620100,
        1620200,
        2620400,
        2620100,
        2620200,
    ]

    family = peakrace_schedule.project_peakrace_schedules(
        [{"activityId": 32620001}], definitions
    )[0]
    assert [item["configuredWeekday"] for item in family["stages"]] == [
        2, 3, 4, 5, 6, 7,
    ]
    assert [item["roundIndex"] for item in family["stages"]] == [1, 1, 1, 2, 2, 2]
    assert family["configurationComplete"] is True

    outer["follow"][-1] = 2620100
    family = peakrace_schedule.project_peakrace_schedules(
        [{"activityId": 32620001}], definitions
    )[0]
    assert family["configurationComplete"] is False


def test_enrichment_failure_preserves_original_runtime_schedule(monkeypatch) -> None:
    schedule = {"available": True, "items": [{"activityId": 32620001}]}
    monkeypatch.setattr(
        peakrace_schedule,
        "load_activity_definitions",
        lambda _root=None: (_ for _ in ()).throw(ValueError("bad config")),
    )

    enriched = peakrace_schedule.enrich_schedule_with_peakrace(schedule)

    assert enriched["available"] is True
    assert enriched["items"] == schedule["items"]
    assert enriched["peakraceScheduleComplete"] is False
    assert enriched["peakraceSchedules"] == []
    assert enriched["peakraceScheduleReason"] == "bad config"


def test_public_runtime_schedule_exposes_peakrace_family(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_schedule,
        "read_worldline_activity_runtime_snapshot",
        lambda **_kwargs: {
            "available": True,
            "complete": True,
            "captured_at": "2026-09-01T05:10:00+08:00",
            "items": [{"activityId": 32620001, "activityType": 79}],
        },
    )
    monkeypatch.setattr(
        peakrace_schedule,
        "load_activity_definitions",
        lambda _root=None: _definitions(),
    )

    schedule = runtime_schedule.read_fanxiu_activity_runtime_schedule()

    assert schedule["runtime_current"] is True
    assert schedule["peakraceScheduleComplete"] is True
    assert schedule["peakraceScheduleCount"] == 1
    assert schedule["peakraceSchedules"][0]["outerActivityId"] == 32620001
