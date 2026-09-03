from __future__ import annotations

"""Read and combine strictly read-only Peak Race business facts."""

from datetime import datetime
from typing import Any, Mapping

from backend.core.fanxiu.activity.peakrace_state import (
    project_peakrace_business_state,
)
from backend.core.fanxiu.activity.runtime_schedule import (
    read_fanxiu_activity_runtime_schedule,
)
from backend.core.fanxiu.instrumentation.peakrace import (
    read_peakrace_runtime_snapshot,
)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _source_identity(snapshot: Mapping[str, Any]) -> tuple[int, int] | None:
    evidence = snapshot.get("evidence")
    if not isinstance(evidence, Mapping):
        evidence = snapshot.get("source_evidence")
    if not isinstance(evidence, Mapping):
        return None
    pid = _integer(evidence.get("pid"))
    process_start_ticks = _integer(evidence.get("process_start_ticks"))
    if pid is None or process_start_ticks is None:
        return None
    return pid, process_start_ticks


def _source_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    identity = _source_identity(snapshot)
    return {
        "available": bool(snapshot.get("available")),
        "complete": bool(snapshot.get("complete")),
        "source_kind": str(snapshot.get("source_kind") or "") or None,
        "captured_at": str(
            snapshot.get("captured_at") or snapshot.get("created_at") or ""
        )
        or None,
        "error_code": str(snapshot.get("error_code") or "") or None,
        "reason": str(snapshot.get("reason") or "") or None,
        "pid": identity[0] if identity is not None else None,
        "process_start_ticks": identity[1] if identity is not None else None,
    }


def _rank_source_summaries(
    rank_snapshots: Mapping[int, Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    return [
        {
            "requested_activity_id": _integer(activity_id),
            "rank_activity_id": _integer(snapshot.get("rank_activity_id")),
            **_source_summary(snapshot),
        }
        for activity_id, snapshot in sorted((rank_snapshots or {}).items())
        if isinstance(snapshot, Mapping)
    ]


def _rank_snapshot_error(
    rank_snapshots: Mapping[int, Mapping[str, Any]] | None,
    *,
    expected_identity: tuple[int, int] | None,
) -> tuple[str, str] | None:
    for raw_activity_id, snapshot in (rank_snapshots or {}).items():
        activity_id = _integer(raw_activity_id)
        if activity_id is None or not isinstance(snapshot, Mapping):
            return "rank_snapshot_invalid", "Rank snapshot keys and values must be typed mappings"
        if _integer(snapshot.get("rank_activity_id")) != activity_id:
            return "rank_activity_mismatch", f"Rank snapshot does not describe activity {activity_id}"
        identity = _source_identity(snapshot)
        if identity is None:
            return "rank_source_identity_missing", f"Rank snapshot {activity_id} has no process identity"
        if expected_identity is None or identity != expected_identity:
            return "rank_source_identity_mismatch", f"Rank snapshot {activity_id} came from a different process"
        captured_at = snapshot.get("captured_at")
        try:
            observed_at = datetime.fromisoformat(str(captured_at))
        except (TypeError, ValueError):
            return "rank_capture_time_missing", f"Rank snapshot {activity_id} has no valid capture time"
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return "rank_capture_time_naive", f"Rank snapshot {activity_id} capture time is not timezone-aware"
    return None


def _failed_snapshot(
    *,
    now: datetime,
    error_code: str,
    reason: str,
    schedule: Mapping[str, Any],
    peakrace_snapshot: Mapping[str, Any],
    source_identity_coherent: bool | None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "complete": False,
        "source_kind": "peakrace_business_state_composite",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "business_time": now.isoformat(timespec="seconds"),
        "error_code": error_code,
        "reason": reason,
        "state": None,
        "sources": {
            "identity_coherent": source_identity_coherent,
            "activity_schedule": _source_summary(schedule),
            "peakrace_runtime": _source_summary(peakrace_snapshot),
        },
    }


def compose_peakrace_business_state_snapshot(
    schedule: Mapping[str, Any],
    peakrace_snapshot: Mapping[str, Any],
    *,
    now: datetime,
    rank_snapshots: Mapping[int, Mapping[str, Any]] | None = None,
    guess_limits: Mapping[int, int] | None = None,
    guess_required: bool | None = None,
    guess_deadlines: Mapping[int, datetime] | None = None,
    worship_daily_limit: int | None = None,
    reward_claimed: bool | None = None,
    ui_scene_id: int | None = None,
) -> dict[str, Any]:
    """Combine already-read inputs without invoking Runtime or game methods.

    Optional policy and completion fields must be explicit observations or
    caller policy.  Their default is unknown; this function never invents a
    guess, worship, or reward strategy.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        return _failed_snapshot(
            now=now,
            error_code="invalid_business_time",
            reason="Peak Race business time must be timezone-aware",
            schedule=schedule,
            peakrace_snapshot=peakrace_snapshot,
            source_identity_coherent=None,
        )

    schedule_identity = _source_identity(schedule)
    peakrace_identity = _source_identity(peakrace_snapshot)
    both_runtime_available = bool(
        schedule.get("available") and peakrace_snapshot.get("available")
    )
    if both_runtime_available and (
        schedule_identity is None or peakrace_identity is None
    ):
        return _failed_snapshot(
            now=now,
            error_code="source_identity_missing",
            reason="Available Runtime inputs do not both carry process identity",
            schedule=schedule,
            peakrace_snapshot=peakrace_snapshot,
            source_identity_coherent=None,
        )
    if (
        schedule_identity is not None
        and peakrace_identity is not None
        and schedule_identity != peakrace_identity
    ):
        return _failed_snapshot(
            now=now,
            error_code="source_identity_mismatch",
            reason="Activity schedule and Peak Race data came from different processes",
            schedule=schedule,
            peakrace_snapshot=peakrace_snapshot,
            source_identity_coherent=False,
        )

    rank_error = _rank_snapshot_error(
        rank_snapshots,
        expected_identity=schedule_identity or peakrace_identity,
    )
    if rank_error is not None:
        error_code, reason = rank_error
        failed = _failed_snapshot(
            now=now,
            error_code=error_code,
            reason=reason,
            schedule=schedule,
            peakrace_snapshot=peakrace_snapshot,
            source_identity_coherent=(
                True
                if schedule_identity is not None and peakrace_identity is not None
                else None
            ),
        )
        failed["sources"]["rank_snapshots"] = _rank_source_summaries(rank_snapshots)
        return failed

    try:
        state = project_peakrace_business_state(
            schedule,
            peakrace_snapshot,
            now=now,
            rank_snapshots=rank_snapshots,
            guess_limits=guess_limits,
            guess_required=guess_required,
            guess_deadlines=guess_deadlines,
            worship_daily_limit=worship_daily_limit,
            reward_claimed=reward_claimed,
            ui_scene_id=ui_scene_id,
        )
    except Exception as exc:
        return _failed_snapshot(
            now=now,
            error_code="projection_failed",
            reason=f"{type(exc).__name__}: {exc}",
            schedule=schedule,
            peakrace_snapshot=peakrace_snapshot,
            source_identity_coherent=(
                True
                if schedule_identity is not None and peakrace_identity is not None
                else None
            ),
        )

    completion = state.get("completion")
    observation_complete = bool(
        isinstance(completion, Mapping)
        and completion.get("observation_complete") is True
    )
    available = bool(state.get("variant") and state.get("outer_activity_id"))
    return {
        "ok": available,
        "available": available,
        "complete": observation_complete,
        "source_kind": "peakrace_business_state_composite",
        "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "business_time": now.isoformat(timespec="seconds"),
        "error_code": None if available else "business_state_unavailable",
        "reason": (
            None
            if available
            else "No unambiguous supported Peak Race occurrence was found"
        ),
        "state": state,
        "sources": {
            "identity_coherent": (
                True
                if schedule_identity is not None and peakrace_identity is not None
                else None
            ),
            "activity_schedule": _source_summary(schedule),
            "peakrace_runtime": _source_summary(peakrace_snapshot),
            "rank_snapshots": _rank_source_summaries(rank_snapshots),
        },
    }


def read_peakrace_business_state_snapshot(
    *,
    now: datetime | None = None,
    allow_schedule_discovery: bool = False,
    force_refresh: bool = False,
    rank_snapshots: Mapping[int, Mapping[str, Any]] | None = None,
    guess_limits: Mapping[int, int] | None = None,
    guess_required: bool | None = None,
    guess_deadlines: Mapping[int, datetime] | None = None,
    worship_daily_limit: int | None = None,
    reward_claimed: bool | None = None,
    ui_scene_id: int | None = None,
) -> dict[str, Any]:
    """Read current read-only inputs and return one Peak Race business snapshot.

    This entry point performs memory reads only.  It does not call Lua methods,
    send requests, navigate UI, or execute any game action.
    """

    business_time = now or datetime.now().astimezone()
    try:
        schedule = read_fanxiu_activity_runtime_schedule(
            allow_discovery=allow_schedule_discovery,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        schedule = {
            "available": False,
            "complete": False,
            "source_kind": "worldline_activity_runtime_memory",
            "error_code": "schedule_read_failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    try:
        peakrace_snapshot = read_peakrace_runtime_snapshot(
            force_refresh=force_refresh,
        )
    except Exception as exc:
        peakrace_snapshot = {
            "available": False,
            "complete": False,
            "source_kind": "peakrace_runtime_memory",
            "error_code": "peakrace_read_failed",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return compose_peakrace_business_state_snapshot(
        schedule,
        peakrace_snapshot,
        now=business_time,
        rank_snapshots=rank_snapshots,
        guess_limits=guess_limits,
        guess_required=guess_required,
        guess_deadlines=guess_deadlines,
        worship_daily_limit=worship_daily_limit,
        reward_claimed=reward_claimed,
        ui_scene_id=ui_scene_id,
    )


__all__ = [
    "compose_peakrace_business_state_snapshot",
    "read_peakrace_business_state_snapshot",
]
