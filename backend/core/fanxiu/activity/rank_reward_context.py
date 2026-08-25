from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from backend.models import (
    FanxiuExchangeActivity,
    FanxiuPacketBusinessRecord,
)


_DAY_MS = 86_400_000


def _positive_ms(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return result if result >= 1_000_000_000_000 else 0


def latest_open_server_time_ms(session: Session) -> int:
    """Open-server time is unavailable until ActivityMgr Runtime exposes it.

    Raw decoded history is deliberately not consulted.  Callers safely omit
    server-day filtering until the Runtime gap is implemented.
    """

    del session
    return 0


def _persisted_schedule_occurrences() -> list[dict[str, Any]]:
    from backend.core.fanxiu.activity.daily_activity_sync import (
        load_worldline_activity_schedule_snapshot,
    )

    snapshot = load_worldline_activity_schedule_snapshot()
    return [
        dict(item)
        for item in snapshot.get("occurrences") or []
        if isinstance(item, dict)
    ]


def server_day_for_start_time(session: Session, start_time_ms: Any) -> int | None:
    """Calculate the authoritative server day for one activity occurrence."""

    start_ms = _positive_ms(start_time_ms)
    if not start_ms:
        return None
    open_ms = latest_open_server_time_ms(session)
    if not open_ms or start_ms < open_ms:
        return None
    return int((start_ms - open_ms) // _DAY_MS) + 1


def _same_period(activity: FanxiuExchangeActivity, start_ms: int, end_ms: int) -> bool:
    if not start_ms or not end_ms:
        return False
    start_date = datetime.fromtimestamp(start_ms / 1000).astimezone().date().isoformat()
    end_date = datetime.fromtimestamp(end_ms / 1000).astimezone().date().isoformat()
    return start_date == activity.start_date and end_date == activity.end_date


def _occurrence_values(
    session: Session,
    activity: FanxiuExchangeActivity,
) -> tuple[int, int]:
    """Resolve start time/world level without probing or mutating the game."""

    evidence = dict(activity.evidence or {})
    start_ms = _positive_ms(evidence.get("period_start_time_ms"))
    world_level = int(evidence.get("world_level") or 0)
    if start_ms:
        return start_ms, world_level

    game_activity_id = int(evidence.get("game_activity_id") or 0)
    if game_activity_id:
        rows = session.exec(
            select(FanxiuPacketBusinessRecord)
            .where(
                FanxiuPacketBusinessRecord.domain == "worldline_activity",
                FanxiuPacketBusinessRecord.entity_id == str(game_activity_id),
            )
            .order_by(col(FanxiuPacketBusinessRecord.captured_at).desc())
        ).all()
        for row in rows:
            payload = row.payload if isinstance(row.payload, dict) else {}
            item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
            candidate_start = _positive_ms(item.get("startTime"))
            candidate_end = _positive_ms(item.get("endTime"))
            if _same_period(activity, candidate_start, candidate_end):
                return candidate_start, world_level or int(item.get("avgWorldLevel") or 0)

        for occurrence in _persisted_schedule_occurrences():
            item = occurrence.get("raw") if isinstance(occurrence.get("raw"), dict) else occurrence
            if int(item.get("activityId") or occurrence.get("activity_id") or 0) != game_activity_id:
                continue
            candidate_start = _positive_ms(item.get("startTime"))
            candidate_end = _positive_ms(item.get("endTime"))
            if _same_period(activity, candidate_start, candidate_end):
                return candidate_start, world_level or int(item.get("avgWorldLevel") or 0)
    return 0, world_level


def resolve_exchange_rank_reward_context(
    session: Session,
    activity: FanxiuExchangeActivity,
) -> dict[str, int | None]:
    """Resolve server-day/world-level filters for one stored activity.

    Existing evidence wins. Missing values are reconstructed strictly from
    the same persisted occurrence and the saved worldline open-server fact.
    """

    evidence = dict(activity.evidence or {})
    server_day = int(evidence.get("server_day") or 0)
    world_level = int(evidence.get("world_level") or 0)
    if not server_day:
        start_ms, resolved_world_level = _occurrence_values(session, activity)
        server_day = server_day_for_start_time(session, start_ms) or 0
        world_level = world_level or resolved_world_level
    return {
        "server_day": server_day or None,
        "world_level": world_level or None,
    }
