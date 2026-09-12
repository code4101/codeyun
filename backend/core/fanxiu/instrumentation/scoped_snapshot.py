"""Finish client Runtime observations before the owning Task releases its device."""
from __future__ import annotations

import time
from collections.abc import Callable


def read_scoped_snapshot(read: Callable[[], dict], *, max_age_seconds: float) -> dict:
    """Read synchronously without sharing the host's background snapshot cache."""
    result = dict(read())
    captured = result.get("captured_at_epoch")
    age = max(0.0, time.time() - captured) if isinstance(captured, (int, float)) else None
    fresh = age is not None and age <= max(0.0, max_age_seconds)
    result.update(cache_age_seconds=age, fresh=fresh, refreshing=False)
    if not fresh:
        result.update(ok=False, available=False)
        result.setdefault("reason", "客户端 Runtime 快照未形成新鲜观察")
    return result
