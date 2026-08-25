from __future__ import annotations

"""Incremental seat-displacement mail facts for the engineering patrol Cell."""

from datetime import datetime, time
from pathlib import Path
import threading
from typing import Any, Callable

from backend.core.fanxiu.data_annotation.state import (
    read_data_annotation_json,
    write_data_annotation_json,
)
from backend.core.fanxiu.instrumentation.mail import read_mail_header_snapshot
from backend.core.settings import get_settings


SEAT_MAIL_PROBE_ID = "seat-displacement-mail"
LUNDAO_SEAT_TASK_ID = "daily-lundao-seat"
LINGMAI_SEAT_TASK_ID = "daily-lingmai-seat"
DONGTIAN_SEATING_TASK_ID = "dongtian-seating"
SEAT_MAIL_WINDOW_START = time(15, 30)
SEAT_MAIL_WINDOW_END = time(22, 0)
SEAT_MAIL_HEADER_LIMIT = 24

# These are positive displacement mail families.  The two ``plundered``
# variants (2104/2205) also state that the player was removed from the seat.
# Dongtian displacement mail is only a trigger.  The Job always re-reads the
# current Dongtian Runtime instead of trusting mail parameters as action facts.
SEAT_DISPLACEMENT_MAIL_DOMAINS: dict[int, str] = {
    2101: "lundao",
    2104: "lundao",
    2105: "lundao",
    2107: "lundao",
    2109: "lundao",
    2112: "lundao",
    2114: "lundao",
    2202: "lingmai",
    2205: "lingmai",
    2206: "lingmai",
    2208: "lingmai",
    2211: "lingmai",
    2213: "lingmai",
    67003: "dongtian",
    67004: "dongtian",
}

_cursor_lock = threading.RLock()


def seat_mail_cursor_state_path() -> Path:
    path = get_settings().data_dir / "fanxiu" / "data-annotation" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path / "seat_mail_inspection_cursor.json"


def seat_mail_patrol_window_active(at: datetime) -> bool:
    clock = at.time().replace(tzinfo=None)
    return SEAT_MAIL_WINDOW_START <= clock < SEAT_MAIL_WINDOW_END


def _baseline_payload(snapshot: dict[str, Any], at: datetime) -> dict[str, Any]:
    head = snapshot.get("head") if isinstance(snapshot.get("head"), dict) else {}
    return {
        "version": 2,
        "head_id": str(head.get("id") or ""),
        "head_create_time": int(head.get("create_time") or 0),
        "mail_total": int(snapshot.get("total") or 0),
        "updated_at": at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _events_from_headers(headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "mail_id": str(row.get("id") or ""),
            "mail_type": int(row.get("type") or 0),
            "create_time": int(row.get("create_time") or 0),
            "domain": SEAT_DISPLACEMENT_MAIL_DOMAINS[int(row.get("type") or 0)],
        }
        for row in headers
        if int(row.get("type") or 0) in SEAT_DISPLACEMENT_MAIL_DOMAINS
    ]


def _due_task_ids(events: list[dict[str, Any]]) -> list[str]:
    domains = {str(event.get("domain") or "") for event in events}
    result: list[str] = []
    if "lundao" in domains:
        result.append(LUNDAO_SEAT_TASK_ID)
    if "lingmai" in domains:
        result.append(LINGMAI_SEAT_TASK_ID)
    if "dongtian" in domains:
        result.append(DONGTIAN_SEATING_TASK_ID)
    return result


def _task_id_for_domain(domain: str) -> str:
    return {
        "lundao": LUNDAO_SEAT_TASK_ID,
        "lingmai": LINGMAI_SEAT_TASK_ID,
        "dongtian": DONGTIAN_SEATING_TASK_ID,
    }.get(domain, "")


def _read_scheduler_task_states() -> list[dict[str, Any]]:
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        read_scheduler_tasks,
    )

    return read_scheduler_tasks()


def _time_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return 0


def _pending_events(
    previous: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    pending = {
        str(task_id): dict(event)
        for task_id, event in (previous.get("pending_events") or {}).items()
        if str(task_id) and isinstance(event, dict)
    }
    for event in events:
        task_id = _task_id_for_domain(str(event.get("domain") or ""))
        if not task_id:
            continue
        old = pending.get(task_id, {})
        if int(event.get("create_time") or 0) >= int(old.get("create_time") or 0):
            pending[task_id] = dict(event)
    return pending


def _reconcile_pending_events(
    pending: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    task_by_id = {
        str(task.get("id") or ""): task
        for task in tasks
        if isinstance(task, dict) and str(task.get("id") or "")
    }
    unresolved: dict[str, dict[str, Any]] = {}
    due_task_ids: list[str] = []
    for task_id, event in pending.items():
        task = task_by_id.get(task_id, {})
        event_time = int(event.get("create_time") or 0)
        completed_after_event = (
            str(task.get("last_result") or "") == "success"
            and _time_ms(task.get("last_run_at")) >= event_time > 0
        )
        if completed_after_event:
            continue
        unresolved[task_id] = event
        due_task_ids.append(task_id)
    ordered = [
        task_id
        for task_id in (
            LUNDAO_SEAT_TASK_ID,
            LINGMAI_SEAT_TASK_ID,
            DONGTIAN_SEATING_TASK_ID,
        )
        if task_id in due_task_ids
    ]
    return unresolved, ordered


def inspect_seat_displacement_mail_state(
    *,
    at: datetime | None = None,
    state_path: Path | None = None,
    reader=read_mail_header_snapshot,
    scheduler_reader: Callable[[], list[dict[str, Any]]] = _read_scheduler_task_states,
    defer_cursor_commit: bool = False,
) -> dict[str, Any]:
    """Consume only mail headers newer than the persisted head cursor."""

    checked_at = at or datetime.now()
    if not seat_mail_patrol_window_active(checked_at):
        return {
            "facts": {
                "active": False,
                "status": "outside_window",
                "window": "15:30-22:00",
            },
            "due_task_ids": [],
        }

    path = state_path or seat_mail_cursor_state_path()
    with _cursor_lock:
        previous = read_data_annotation_json(path, {})
        previous = dict(previous) if isinstance(previous, dict) else {}
        snapshot = reader(limit=SEAT_MAIL_HEADER_LIMIT)
        if not bool(snapshot.get("ok") and snapshot.get("complete")):
            reason = str(snapshot.get("reason") or "mail headers unavailable")
            return {
                "ok": False,
                "message": f"座位邮件 Runtime 不可用：{reason}",
                "facts": {
                    "active": True,
                    "status": "runtime_unavailable",
                    "window": "15:30-22:00",
                    "reason": reason,
                },
                "due_task_ids": [],
                "recovery_required": False,
            }

        current = _baseline_payload(snapshot, checked_at)
        headers = [row for row in snapshot.get("items") or [] if isinstance(row, dict)]
        previous_head = str(previous.get("head_id") or "")
        status = "unchanged"
        new_headers: list[dict[str, Any]] = []
        if not previous_head:
            window_start_ms = int(
                datetime.combine(checked_at.date(), SEAT_MAIL_WINDOW_START).timestamp()
                * 1000
            )
            new_headers = [
                row
                for row in headers
                if int(row.get("create_time") or 0) >= window_start_ms
            ]
            status = "new_mail" if new_headers else "baseline_created"
        elif previous_head != current["head_id"]:
            previous_index = next(
                (index for index, row in enumerate(headers) if str(row.get("id") or "") == previous_head),
                None,
            )
            if previous_index is not None:
                new_headers = headers[:previous_index]
                status = "new_mail" if new_headers else "unchanged"
            else:
                # A deleted/reordered list or a gap larger than the bounded
                # prefix cannot be interpreted as new mail safely.  Rebase
                # without replaying old messages.
                status = "cursor_rebased"

        # Version-1 cursors may already point at a displacement mail whose due
        # write was later overwritten.  Reconcile that bounded same-day prefix
        # once during migration, then persist responsibility in pending_events.
        event_headers = list(new_headers)
        if previous_head == current["head_id"] and int(previous.get("version") or 1) < 2:
            window_start_ms = int(
                datetime.combine(checked_at.date(), SEAT_MAIL_WINDOW_START).timestamp()
                * 1000
            )
            event_headers.extend(
                row for row in headers if int(row.get("create_time") or 0) >= window_start_ms
            )
        events = _events_from_headers(event_headers)
        pending = _pending_events(previous, events)
        pending, due_task_ids = _reconcile_pending_events(pending, scheduler_reader())
        current["pending_events"] = pending
        pending_task_ids = [
            task_id
            for task_id in (
                LUNDAO_SEAT_TASK_ID,
                LINGMAI_SEAT_TASK_ID,
                DONGTIAN_SEATING_TASK_ID,
            )
            if task_id in pending
        ]
        domains = {event["domain"] for event in events}
        result = {
            "ok": True,
            "facts": {
                "active": True,
                "status": status,
                "window": "15:30-22:00",
                "mail_total": current["mail_total"],
                "new_mail_count": len(new_headers),
                "events": events,
                "pending_task_ids": pending_task_ids,
                "dongtian_downstream": "pass" if "dongtian" in domains else "not_observed",
            },
            "due_task_ids": due_task_ids,
        }
        return _with_cursor_commit(
            result,
            path=path,
            cursor=current,
            deferred=defer_cursor_commit,
        )


def _with_cursor_commit(
    result: dict[str, Any],
    *,
    path: Path,
    cursor: dict[str, Any],
    deferred: bool,
) -> dict[str, Any]:
    """Commit the mail cursor now or hand a one-shot commit to the patrol.

    The engineering patrol defers this write until every emitted ``next_time``
    update succeeds.  Direct diagnostic callers keep the historical immediate
    commit behaviour.
    """

    if not deferred:
        write_data_annotation_json(path, cursor)
        return result

    def commit() -> None:
        with _cursor_lock:
            write_data_annotation_json(path, cursor)

    return {**result, "_commit_after_due": commit}


__all__ = [
    "SEAT_DISPLACEMENT_MAIL_DOMAINS",
    "SEAT_MAIL_PROBE_ID",
    "DONGTIAN_SEATING_TASK_ID",
    "inspect_seat_displacement_mail_state",
    "seat_mail_patrol_window_active",
]
