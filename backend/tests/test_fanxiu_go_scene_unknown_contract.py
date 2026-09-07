from __future__ import annotations

import threading

from backend.core.fanxiu.data_annotation import behavior_tree_executor as executor_module
from backend.core.fanxiu.behavior_tree.kernel_scheduler import (
    create_behavior_tree_executor,
)


def test_go_scene_unknown_requires_full_continuous_budget(monkeypatch) -> None:
    """A transient return animation must not receive an immediate #424 action."""

    runner = create_behavior_tree_executor()
    clock = {"now": 0.0}

    class UnknownContext:
        def current_scene(self, *_args, **_kwargs):
            if False:
                yield None
            return None, 0.0, "frame"

    def settle(*_args, seconds: float, **_kwargs):
        clock["now"] += seconds
        if False:
            yield None

    monkeypatch.setattr(executor_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(runner, "_wait_action_settle", settle)
    monkeypatch.setattr(
        runner,
        "_identify_scene_number_for_route",
        lambda *_args, **_kwargs: (None, 0.0),
    )
    monkeypatch.setattr(runner, "_commit_scene_observation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_log", lambda *_args, **_kwargs: None)

    iterator = runner._wait_for_go_scene_recognition(
        {},
        UnknownContext(),
        [],
        34,
        threading.Event(),
        "frame",
        wait_seconds=60.0,
        max_wait_seconds=60.0,
    )
    try:
        while True:
            next(iterator)
    except StopIteration as stopped:
        scene_id, _score, _frame, status = stopped.value

    assert scene_id is None
    assert status == "continuous_unknown"
    assert clock["now"] >= 60.0
