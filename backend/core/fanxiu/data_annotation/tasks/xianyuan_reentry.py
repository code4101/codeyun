from __future__ import annotations

from typing import Any, Mapping, MutableMapping


DAILY_XIANYUAN_REENTRY_REQUESTED = object()
DAILY_XIANYUAN_SHARED_DEADLINE_KEY = "__daily_xianyuan_shared_deadline"
DEFAULT_DAILY_XIANYUAN_MAX_REENTRIES = 1
MAX_DAILY_XIANYUAN_MAX_REENTRIES = 2


def daily_xianyuan_reentry_limit(payload: Mapping[str, Any]) -> int:
    """Return the bounded number of whole-task reentries for one Job attempt."""

    raw_value = payload.get(
        "xianyuan_max_reentries",
        DEFAULT_DAILY_XIANYUAN_MAX_REENTRIES,
    )
    value = int(raw_value)
    return min(MAX_DAILY_XIANYUAN_MAX_REENTRIES, max(0, value))


def daily_xianyuan_shared_deadline(
    payload: MutableMapping[str, Any],
    *,
    now: float,
) -> float:
    """Create once and then reuse the dialogue/challenge deadline across reentries."""

    existing = payload.get(DAILY_XIANYUAN_SHARED_DEADLINE_KEY)
    if isinstance(existing, (int, float)):
        return float(existing)

    timeout = max(0.0, float(payload.get("attack_dialogue_timeout") or 45.0))
    deadline = now + timeout
    payload[DAILY_XIANYUAN_SHARED_DEADLINE_KEY] = deadline
    return deadline


__all__ = [
    "DAILY_XIANYUAN_REENTRY_REQUESTED",
    "DAILY_XIANYUAN_SHARED_DEADLINE_KEY",
    "DEFAULT_DAILY_XIANYUAN_MAX_REENTRIES",
    "MAX_DAILY_XIANYUAN_MAX_REENTRIES",
    "daily_xianyuan_reentry_limit",
    "daily_xianyuan_shared_deadline",
]
