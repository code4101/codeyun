from __future__ import annotations

from typing import Any

from backend.core.fanxiu.instrumentation.activity_runtime import (
    read_worldline_activity_runtime_snapshot,
)


def read_fanxiu_activity_runtime_schedule(
    *,
    allow_discovery: bool = False,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Read the current #66 schedule directly from the game Runtime."""

    snapshot = read_worldline_activity_runtime_snapshot(
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
    )
    return {
        **snapshot,
        "available": bool(snapshot.get("available")),
        "created_at": str(snapshot.get("captured_at") or ""),
        "runtime_current": bool(snapshot.get("available") and snapshot.get("complete")),
    }


def get_cached_fanxiu_activity_runtime_schedule(**_kwargs: Any) -> dict[str, Any]:
    """Compatibility name for callers; no packet or filesystem cache is read."""

    return read_fanxiu_activity_runtime_schedule()


def refresh_cached_fanxiu_activity_runtime_schedule(
    *,
    allow_discovery: bool = False,
    force_refresh: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Refresh the live Runtime projection without any capture fallback."""

    return read_fanxiu_activity_runtime_schedule(
        allow_discovery=allow_discovery,
        force_refresh=force_refresh,
    )


__all__ = [
    "get_cached_fanxiu_activity_runtime_schedule",
    "read_fanxiu_activity_runtime_schedule",
    "refresh_cached_fanxiu_activity_runtime_schedule",
]
