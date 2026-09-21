from __future__ import annotations

import pytest

from backend.core.fanxiu.instrumentation.bothdraw import (
    build_bothdraw_task_snapshot,
)
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def test_fast_reader_entry_shape_maps_to_existing_contract() -> None:
    """The validated fast reader emits ``taskId/status/turn/rewardTime/targetTurn``."""

    snapshot = build_bothdraw_task_snapshot(
        activity_id=30402,
        task_configs=[
            {"id": 3040201, "name": "圣木祈愿·初", "desc": "d1", "sort": 1},
            {"id": 3040202, "name": "圣木祈愿·二", "desc": "d2", "sort": 2},
        ],
        task_entries=[
            {
                "taskId": 3040201,
                "status": 4,
                "turn": 1,
                "rewardTime": 1,
                "targetTurn": 1,
                "progressList": [],
            }
        ],
        finished_task_ids=[3040202],
    )

    by_id = {task["task_id"]: task for task in snapshot["tasks"]}
    assert snapshot["activity_id"] == 30402
    assert snapshot["task_count"] == 2
    assert snapshot["claimable_count"] == 1
    assert snapshot["claimed_count"] == 1
    assert by_id[3040201]["state"] == "claimable"
    assert by_id[3040201]["target_turn"] == 1
    assert by_id[3040202]["state"] == "claimed"


def test_fast_reader_missing_entry_still_fails_closed() -> None:
    with pytest.raises(FanxiuRuntimeMemoryError, match="任务状态不完整"):
        build_bothdraw_task_snapshot(
            activity_id=30402,
            task_configs=[{"id": 3040201, "name": "圣木祈愿·初", "sort": 1}],
            task_entries=[],
            finished_task_ids=[],
        )
