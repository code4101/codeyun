from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


_MISSING = object()


@dataclass
class _SchedulingDecision:
    task_id: str
    wrote_own_next_time: bool = False


_ACTIVE_SCHEDULING_DECISION: ContextVar[_SchedulingDecision | None] = ContextVar(
    "fanxiu_active_scheduling_decision",
    default=None,
)


def mark_scheduler_next_time_written(task_id: str) -> None:
    """Mark a business-owned next_time write for the active scheduled Job."""

    active = _ACTIVE_SCHEDULING_DECISION.get()
    if active is not None and active.task_id == str(task_id or "").strip():
        active.wrote_own_next_time = True


@contextmanager
def execution_task_payload(
    execution_ctx: dict[str, Any],
    payload: dict[str, Any],
    *,
    require_scheduling_decision: bool = False,
) -> Iterator[None]:
    """Expose one Job Cell payload to every behavior-tree context created from it.

    The Scheduler-owned Job id travels in the ordinary Cell payload.  Behavior-tree
    business completion points read that same task-scoped payload from
    ``ctx.attrs``; keeping the binding here avoids global state, label lookup,
    and per-Job fallback ids.  Nested scopes restore their caller exactly, and
    the ``finally`` path also covers generator errors and interrupts.
    """

    attrs = execution_ctx.get("attrs")
    created_attrs = not isinstance(attrs, dict)
    if created_attrs:
        attrs = {}
        execution_ctx["attrs"] = attrs
    previous_payload = attrs.get("payload", _MISSING)
    attrs["payload"] = dict(payload)
    task_id = str(payload.get("__scheduler_task_id") or "").strip()
    decision_token = None
    decision = None
    if require_scheduling_decision and task_id:
        decision = _SchedulingDecision(task_id=task_id)
        decision_token = _ACTIVE_SCHEDULING_DECISION.set(decision)
    try:
        yield
    except BaseException:
        raise
    else:
        if decision is not None and not decision.wrote_own_next_time:
            raise RuntimeError(
                f"标准 Task {task_id!r} 正常返回，但未形成自己的 next_time 调度决策"
            )
    finally:
        if decision_token is not None:
            _ACTIVE_SCHEDULING_DECISION.reset(decision_token)
        if previous_payload is _MISSING:
            attrs.pop("payload", None)
        else:
            attrs["payload"] = previous_payload
        if created_attrs and not attrs:
            execution_ctx.pop("attrs", None)
