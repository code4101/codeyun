from __future__ import annotations

from datetime import datetime

import pytest

from pyxllib.prog.behavior_tree import Status as BehaviorTreeStatus

from backend.core.fanxiu.behavior_tree.runtime import (
    create_behavior_tree_runtime_runner,
)
from backend.core.fanxiu.data_annotation import behavior_tree_runtime


def _drain(generator):
    while True:
        try:
            next(generator)
        except StopIteration as exc:
            return exc.value


def test_changed_sanqing_target_does_not_claim_seat_success(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    actions: list[tuple] = []
    scheduled: list[tuple[str, str]] = []

    class Runtime:
        def goto_view(self, scene_id):
            actions.append(("goto_view", scene_id))
            yield BehaviorTreeStatus.RUNNING

    def return_to_selection(_runtime, scene_id):
        actions.append(("return_to_selection", scene_id))
        yield BehaviorTreeStatus.RUNNING
        return 296

    monkeypatch.setattr(
        behavior_tree_runtime,
        "_now",
        lambda: datetime(2026, 8, 16, 15, 57, 4),
    )
    monkeypatch.setattr(
        runner,
        "_return_daily_lundao_to_selection",
        return_to_selection,
    )
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    result = _drain(
        runner._finish_daily_lundao_changed_sanqing_target(
            Runtime(),
            {"__scheduler_task_id": "daily-lundao-seat"},
        )
    )

    assert result == "success"
    assert actions == [("return_to_selection", 297), ("goto_view", 34)]
    assert scheduled == [("daily-lundao-seat", "2026-08-16 16:07:04")]
    assert "已完成三清入座" not in "\n".join(
        str(item.get("message") or "") for item in runner.status().get("logs", [])
    )


@pytest.mark.parametrize(
    "status",
    [
        {"available": True, "complete": True, "seated": False, "room_id": None},
        {"available": True, "complete": True, "seated": True, "room_id": 15},
        {"available": False, "complete": False, "seated": None, "room_id": None},
    ],
)
def test_sanqing_success_is_rejected_until_runtime_confirms_seated(
    monkeypatch,
    status,
) -> None:
    runner = create_behavior_tree_runtime_runner()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    with pytest.raises(RuntimeError, match="Runtime 未确认目标道场"):
        runner._record_confirmed_daily_lundao_seat(
            {"__scheduler_task_id": "daily-lundao-seat"},
            status,
            expected_room_id=14,
            next_time="2026-08-16 16:27:04",
            label="三清入座",
            reason="已完成三清入座",
        )

    assert scheduled == []


def test_sanqing_success_is_recorded_after_runtime_confirms_room_14(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    scheduled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        runner,
        "_persist_scheduler_task_next_time",
        lambda task_id, next_time: scheduled.append((task_id, next_time)),
    )

    runner._record_confirmed_daily_lundao_seat(
        {"__scheduler_task_id": "daily-lundao-seat"},
        {"available": True, "complete": True, "seated": True, "room_id": 14},
        expected_room_id=14,
        next_time="2026-08-16 16:27:04",
        label="三清入座",
        reason="已完成三清入座",
    )

    assert scheduled == [("daily-lundao-seat", "2026-08-16 16:27:04")]
