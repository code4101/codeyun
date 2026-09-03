from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from backend.core.temp_paths import codeyun_temp_root


_FANXIU_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
_UNATTRIBUTED_TASK = "<unattributed>"


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed is None or parsed.tzinfo is None:
        return parsed
    # Action-trace timestamps are legacy local wall-clock values without an
    # offset.  Normalize an aware CLI bound to that same clock before filtering
    # instead of rejecting every otherwise-valid row as a malformed event.
    return parsed.astimezone(_FANXIU_LOCAL_TIMEZONE).replace(tzinfo=None)


def _scene(value: Any) -> int | str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return text


def summarize_action_trace_events(
    events: Iterable[Any],
    *,
    malformed_lines: int = 0,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    session_break_seconds: float = 300.0,
    top_n: int = 20,
) -> dict[str, Any]:
    """Summarize action-to-action gaps without claiming full Job duration.

    The trace contains only actions, so the first pre-action wait and the final
    post-action settle are unobserved.  Sessions and gaps are useful for finding
    slow transitions, but deliberately remain distinct from Scheduler duration.
    """

    since_dt = _parse_time(since)
    until_dt = _parse_time(until)
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise ValueError("since must not be later than until")
    if session_break_seconds <= 0:
        raise ValueError("session_break_seconds must be positive")
    if top_n < 1:
        raise ValueError("top_n must be positive")

    parsed: list[dict[str, Any]] = []
    malformed = max(0, int(malformed_lines))
    unattributed = 0
    for event in events:
        if not isinstance(event, dict):
            malformed += 1
            continue
        raw_time = event.get("time")
        task = event.get("runtime_task")
        try:
            observed_at = _parse_time(str(raw_time))
        except (TypeError, ValueError):
            malformed += 1
            continue
        if observed_at is None:
            malformed += 1
            continue
        try:
            if since_dt is not None and observed_at < since_dt:
                continue
            if until_dt is not None and observed_at > until_dt:
                continue
        except TypeError:
            malformed += 1
            continue
        if not isinstance(task, str) or not task.strip():
            task_label = _UNATTRIBUTED_TASK
            unattributed += 1
        else:
            task_label = task.strip()
        parsed.append(
            {
                "time": observed_at,
                "task": task_label,
                "scene": _scene(event.get("image_number")),
                "phase": str(event.get("phase") or "").strip(),
                "kind": str(event.get("kind") or "").strip(),
                "label": str(
                    (event.get("action") or {}).get("label")
                    if isinstance(event.get("action"), dict)
                    else ""
                ).strip(),
            }
        )

    parsed.sort(key=lambda item: item["time"])
    sessions: list[list[dict[str, Any]]] = []
    for event in parsed:
        if not sessions:
            sessions.append([event])
            continue
        previous = sessions[-1][-1]
        gap = (event["time"] - previous["time"]).total_seconds()
        if event["task"] != previous["task"] or gap > session_break_seconds:
            sessions.append([event])
        else:
            sessions[-1].append(event)

    task_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "session_count": 0,
            "action_count": 0,
            "action_span_seconds": 0.0,
            "max_action_gap_seconds": None,
        }
    )
    gaps: list[dict[str, Any]] = []
    session_rows: list[dict[str, Any]] = []
    for number, session in enumerate(sessions, start=1):
        first = session[0]
        last = session[-1]
        span = (last["time"] - first["time"]).total_seconds()
        row = {
            "session": number,
            "task": first["task"],
            "started_at": first["time"].isoformat(),
            "ended_at": last["time"].isoformat(),
            "action_count": len(session),
            "action_span_seconds": span,
        }
        session_rows.append(row)
        stats = task_stats[first["task"]]
        stats["session_count"] += 1
        stats["action_count"] += len(session)
        stats["action_span_seconds"] += span
        for before, after in zip(session, session[1:]):
            gap = (after["time"] - before["time"]).total_seconds()
            gaps.append(
                {
                    "task": first["task"],
                    "session": number,
                    "gap_seconds": gap,
                    "from_time": before["time"].isoformat(),
                    "to_time": after["time"].isoformat(),
                    "from_scene": before["scene"],
                    "to_scene": after["scene"],
                    "from_phase": before["phase"],
                    "to_phase": after["phase"],
                    "from_label": before["label"],
                    "to_label": after["label"],
                }
            )
            current = stats["max_action_gap_seconds"]
            if current is None or gap > current:
                stats["max_action_gap_seconds"] = gap

    ordered_gaps = sorted(gaps, key=lambda item: item["gap_seconds"], reverse=True)
    complete = bool(parsed) and malformed == 0
    reason = "" if complete else (
        "malformed_action_trace_events" if parsed else "no_valid_action_trace_events"
    )
    return {
        "ok": complete,
        "complete": complete,
        "reason": reason,
        "event_count": len(parsed),
        "malformed_event_count": malformed,
        "unattributed_event_count": unattributed,
        "session_count": len(sessions),
        "limitations": [
            "action spans exclude waits before the first and after the last action",
            "a large gap identifies a transition to inspect, not its root cause",
            "events without runtime_task form separate unattributed sessions",
        ],
        "tasks": dict(sorted(task_stats.items())),
        "sessions": session_rows,
        "top_action_gaps": ordered_gaps[:top_n],
    }


def summarize_action_trace_log(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    trace_path = Path(path)
    if not trace_path.is_file():
        result = summarize_action_trace_events((), **kwargs)
        result["path"] = str(trace_path)
        return result
    events: list[Any] = []
    malformed_lines = 0
    try:
        with trace_path.open("rb") as stream:
            for raw_line in stream:
                if not raw_line.strip():
                    continue
                try:
                    events.append(json.loads(raw_line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    malformed_lines += 1
    except OSError as exc:
        result = summarize_action_trace_events((), **kwargs)
        result.update(
            {
                "reason": "action_trace_log_read_error",
                "read_error": f"{type(exc).__name__}: {exc}",
                "path": str(trace_path),
            }
        )
        return result
    result = summarize_action_trace_events(
        events,
        malformed_lines=malformed_lines,
        **kwargs,
    )
    result["path"] = str(trace_path)
    return result


def default_action_trace_index_path() -> Path:
    return codeyun_temp_root("fanxiu_action_trace") / "index.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize offline Fanxiu action trace gaps")
    parser.add_argument("path", nargs="?", type=Path, default=default_action_trace_index_path())
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--session-break-seconds", type=float, default=300.0)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args(argv)
    result = summarize_action_trace_log(
        args.path,
        since=args.since,
        until=args.until,
        session_break_seconds=args.session_break_seconds,
        top_n=args.top,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
