from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


_MISSING = object()


@contextmanager
def runtime_task_payload(
    runtime_ctx: dict[str, Any],
    payload: dict[str, Any],
) -> Iterator[None]:
    """Expose one Job Cell payload to every Runtime created from its context.

    The Scheduler-owned Job id travels in the ordinary Cell payload.  Runtime
    business completion points read that same task-scoped payload from
    ``ctx.attrs``; keeping the binding here avoids global state, label lookup,
    and per-Job fallback ids.  Nested scopes restore their caller exactly, and
    the ``finally`` path also covers generator errors and interrupts.
    """

    attrs = runtime_ctx.get("attrs")
    created_attrs = not isinstance(attrs, dict)
    if created_attrs:
        attrs = {}
        runtime_ctx["attrs"] = attrs
    previous_payload = attrs.get("payload", _MISSING)
    attrs["payload"] = dict(payload)
    try:
        yield
    finally:
        if previous_payload is _MISSING:
            attrs.pop("payload", None)
        else:
            attrs["payload"] = previous_payload
        if created_attrs and not attrs:
            runtime_ctx.pop("attrs", None)
