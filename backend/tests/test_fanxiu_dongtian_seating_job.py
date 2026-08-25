from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.default_jobs import (
    register_fanxiu_data_annotation_default_runtime_jobs,
)
from backend.core.fanxiu.data_annotation.jobs import (
    list_fanxiu_data_annotation_task_cell_definitions,
)
from backend.core.fanxiu.data_annotation.scheduler_defaults import (
    default_data_annotation_scheduler_tasks,
)
from backend.core.fanxiu.data_annotation.tasks.dongtian_seating_job import (
    classify_dongtian_team_seating,
    choose_dongtian_empty_follower_target,
    execute_dongtian_seating_job,
)


def _snapshot(*, idle=()):
    rows = []
    for team_id in (1, 2, 3):
        if team_id in idle:
            rows.append({"id": team_id, "complete": True, "state": 1, "mine_id": 0, "seat_index": 0})
        else:
            rows.append({"id": team_id, "complete": True, "state": 2, "mine_id": team_id + 4, "seat_index": team_id + 8})
    return {
        "available": True,
        "complete": True,
        "seating_summary_complete": True,
        "teams": rows,
    }


class Runner:
    def __init__(self):
        self.writes = []
        self.logs = []

    def _persist_scheduler_task_next_time(self, task_id, next_time):
        self.writes.append((task_id, next_time))

    def _log(self, level, message):
        self.logs.append((level, message))


def test_team_seating_requires_exactly_three_complete_teams():
    assert classify_dongtian_team_seating(_snapshot())["status"] == "all_seated"
    assert classify_dongtian_team_seating(_snapshot(idle=(2,)))["idle_team_ids"] == [2]
    broken = _snapshot()
    broken["teams"] = broken["teams"][:2]
    assert classify_dongtian_team_seating(broken)["status"] == "runtime_incomplete"


def test_team_seating_uses_complete_seating_summary_even_when_full_snapshot_is_partial():
    snapshot = _snapshot(idle=(3,))
    snapshot["complete"] = False

    result = classify_dongtian_team_seating(snapshot)

    assert result["status"] == "reseat_required"
    assert result["idle_team_ids"] == [3]


def test_all_seated_is_zero_action_success_and_clears_dynamic_schedule():
    runner = Runner()
    result = execute_dongtian_seating_job(
        runner,
        {"__scheduler_task_id": "dongtian-seating"},
        snapshot_reader=lambda: _snapshot(),
    )
    assert result == "success"
    assert runner.writes == [("dongtian-seating", None)]
    assert "1/2/3队全部在座" in runner.logs[-1][1]


def test_idle_team_fails_closed_without_partial_friend_scan():
    runner = Runner()
    with pytest.raises(RuntimeError, match="3队80%安全规则"):
        execute_dongtian_seating_job(
            runner,
            snapshot_reader=lambda: _snapshot(idle=(3,)),
        )
    assert runner.writes == []


def test_empty_follower_fast_path_uses_native_lowest_idle_team_and_other_mine():
    snapshot = _snapshot(idle=(2, 3))
    for row in snapshot["teams"]:
        row.update({"dead": False, "fight_score": 1000 * row["id"]})
    snapshot.update(
        {
            "own_union_id": 77,
            "mines": [
                {
                    "id": 5,
                    "config_group": 4,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [
                        {
                            "id": 6,
                            "quality": 2,
                            "complete": True,
                            "empty": True,
                            "guarder_present": False,
                            "guarder_type": 0,
                        }
                    ],
                },
                {
                    "id": 9,
                    "config_group": 4,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [
                        {
                            "id": 7,
                            "quality": 2,
                            "complete": True,
                            "empty": True,
                            "guarder_present": False,
                            "guarder_type": 0,
                        }
                    ],
                },
            ],
        }
    )

    target = choose_dongtian_empty_follower_target(snapshot)

    # Team 1 already occupies mine 5, so the fast path neither stacks teams
    # into that location nor skips over native idle-team ordering.
    assert target == {
        "mine_id": 9,
        "quality": 2,
        "seat_id": 7,
        "team_id": 2,
        "config_group": 4,
        "mode": "occupy_empty",
    }


def test_empty_follower_fast_path_never_replaces_occupied_seat():
    snapshot = _snapshot(idle=(3,))
    for row in snapshot["teams"]:
        row.update({"dead": False, "fight_score": 1000 * row["id"]})
    snapshot.update(
        {
            "own_union_id": 77,
            "mines": [
                {
                    "id": 20,
                    "config_group": 4,
                    "cross_union_id": 77,
                    "seats_complete": True,
                    "seats": [
                        {
                            "id": 4,
                            "quality": 2,
                            "complete": True,
                            "empty": False,
                            "guarder_present": True,
                            "guarder_type": 1,
                        }
                    ],
                }
            ],
        }
    )

    assert choose_dongtian_empty_follower_target(snapshot) is None


def test_dongtian_seating_is_one_standard_dynamic_job():
    register_fanxiu_data_annotation_default_runtime_jobs()
    registered = {
        item.task_type: item
        for item in list_fanxiu_data_annotation_task_cell_definitions()
    }
    definition = registered["dongtian_seating"]
    assert definition.scheduler_supported is True
    assert definition.standard_job is True
    tasks = [task for task in default_data_annotation_scheduler_tasks() if task["id"] == "dongtian-seating"]
    assert len(tasks) == 1
    assert tasks[0]["next_time"] is None
    assert tasks[0]["trigger_description"] == "动态"
