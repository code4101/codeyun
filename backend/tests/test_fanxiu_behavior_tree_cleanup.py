"""Deterministic execution-lifetime contracts; no game or GUI simulation."""

import threading
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.data_annotation.behavior_tree_container import BehaviorTreeContainer
from backend.core.fanxiu.data_annotation.task_context import (
    _ACTIVE_SCHEDULING_DECISION,
    execution_task_payload,
    mark_scheduler_next_time_written,
)


@pytest.mark.parametrize("failure", [KeyboardInterrupt, RuntimeError])
def test_between_tick_failure_closes_job_before_next_attempt(tmp_path, failure):
    class InterruptedWait(threading.Event):
        def wait(self, timeout=None):
            raise failure("between ticks")

    execution_ctx = {"attrs": {"payload": {"caller": True}}}
    owner = SimpleNamespace(guard_definitions={}, _raise_if_stopped=lambda event: None)
    container = BehaviorTreeContainer(
        owner, execution_ctx=execution_ctx, asset_tree_path=tmp_path,
        stop_event=InterruptedWait(),
    )

    def job():
        with execution_task_payload(
            execution_ctx, {"__scheduler_task_id": "old"}, require_scheduling_decision=True
        ):
            yield 1

    retained_generator = job()  # A traceback/debugger may retain it beyond this Cell.
    with pytest.raises(failure, match="between ticks"):
        container.run_job_until_complete(action=lambda: retained_generator, label="old")
    assert retained_generator.gi_frame is None
    assert _ACTIVE_SCHEDULING_DECISION.get() is None
    assert execution_ctx["attrs"]["payload"] == {"caller": True}
    with execution_task_payload(
        execution_ctx, {"__scheduler_task_id": "new"}, require_scheduling_decision=True
    ):
        retained_generator.close()
        mark_scheduler_next_time_written("new")
    assert _ACTIVE_SCHEDULING_DECISION.get() is None
