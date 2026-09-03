from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from backend.core.settings import get_settings


EVENT_NAME = "adb_input_timing"
STAGE_NAMES = ("ensure", "connect", "wm_size", "input", "fallback")
VALID_OUTCOMES = {"success", "failed", "manager_fallback"}
_FANXIU_LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _stage_summary(values: list[float], *, failure_count: int = 0) -> dict[str, Any]:
    return {
        "count": len(values),
        "p50_seconds": _percentile(values, 0.50),
        "p95_seconds": _percentile(values, 0.95),
        "max_seconds": max(values) if values else None,
        "failure_count": failure_count,
        "failure_rate": failure_count / len(values) if values else None,
    }


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed is None or parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(_FANXIU_LOCAL_TIMEZONE).replace(tzinfo=None)


def _event_in_window(
    event: dict[str, Any],
    *,
    since: datetime | None,
    until: datetime | None,
) -> bool | None:
    if since is None and until is None:
        return True
    raw_time = event.get("time")
    if not isinstance(raw_time, str) or not raw_time.strip():
        return None
    try:
        observed_at = _parse_time(raw_time)
    except ValueError:
        return None
    if observed_at is None:
        return None
    if since is not None and observed_at < since:
        return False
    if until is not None and observed_at > until:
        return False
    return True


def summarize_adb_input_timing_events(
    events: Iterable[Any],
    *,
    malformed_lines: int = 0,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
) -> dict[str, Any]:
    """Aggregate existing ADB timing events without inferring from bad samples."""

    since_dt = _parse_time(since)
    until_dt = _parse_time(until)
    if since_dt is not None and until_dt is not None and since_dt > until_dt:
        raise ValueError("since must not be later than until")

    stage_values: dict[str, list[float]] = {name: [] for name in STAGE_NAMES}
    stage_failures = {name: 0 for name in STAGE_NAMES}
    elapsed_values: list[float] = []
    outcome_counts = {name: 0 for name in sorted(VALID_OUTCOMES)}
    command_counts: dict[str, int] = {}
    valid_events = 0
    malformed_events = max(0, int(malformed_lines))

    for event in events:
        if not isinstance(event, dict):
            malformed_events += 1
            continue
        if event.get("event") != EVENT_NAME:
            continue
        in_window = _event_in_window(event, since=since_dt, until=until_dt)
        if in_window is False:
            continue
        if in_window is None:
            malformed_events += 1
            continue

        outcome = event.get("outcome")
        elapsed = _duration(event.get("elapsed_seconds"))
        command_kind = event.get("command_kind")
        top_stages = event.get("stages_seconds")
        attempts = event.get("attempts")
        if (
            outcome not in VALID_OUTCOMES
            or elapsed is None
            or not isinstance(command_kind, str)
            or not command_kind.strip()
            or not isinstance(top_stages, dict)
            or not isinstance(attempts, list)
        ):
            malformed_events += 1
            continue

        ensure = _duration(top_stages.get("ensure"))
        fallback_raw = top_stages.get("manager_fallback")
        fallback = _duration(fallback_raw) if fallback_raw is not None else None
        if ensure is None or (fallback_raw is not None and fallback is None):
            malformed_events += 1
            continue

        attempt_values: dict[str, list[float]] = {
            "connect": [],
            "wm_size": [],
            "input": [],
        }
        event_stage_failures = {name: 0 for name in STAGE_NAMES}
        valid_attempts = True
        for attempt in attempts:
            if not isinstance(attempt, dict) or not isinstance(attempt.get("stages_seconds"), dict):
                valid_attempts = False
                break
            attempt_outcome = attempt.get("outcome")
            failed_stage = attempt.get("failed_stage")
            if attempt_outcome not in {"success", "failed"}:
                valid_attempts = False
                break
            if attempt_outcome == "failed" and failed_stage not in {
                "connect",
                "wm_size",
                "input",
                "disconnect",
                "retry_settle",
            }:
                valid_attempts = False
                break
            for stage in attempt_values:
                if stage not in attempt["stages_seconds"]:
                    continue
                duration = _duration(attempt["stages_seconds"][stage])
                if duration is None:
                    valid_attempts = False
                    break
                attempt_values[stage].append(duration)
            if not valid_attempts:
                break
            if attempt_outcome == "failed" and failed_stage in event_stage_failures:
                event_stage_failures[str(failed_stage)] += 1
        if not valid_attempts:
            malformed_events += 1
            continue

        valid_events += 1
        elapsed_values.append(elapsed)
        outcome_counts[str(outcome)] += 1
        command_key = command_kind.strip()
        command_counts[command_key] = command_counts.get(command_key, 0) + 1
        for stage, failure_count in event_stage_failures.items():
            stage_failures[stage] += failure_count
        stage_values["ensure"].append(ensure)
        if fallback is not None:
            stage_values["fallback"].append(fallback)
            if outcome == "failed":
                stage_failures["fallback"] += 1
        elif outcome == "failed" and not attempts:
            stage_failures["ensure"] += 1
        for stage, values in attempt_values.items():
            stage_values[stage].extend(values)

    stages = {
        name: _stage_summary(stage_values[name], failure_count=stage_failures[name])
        for name in STAGE_NAMES
    }
    complete = valid_events > 0 and malformed_events == 0
    bottleneck = None
    if complete:
        candidates = [
            (name, details["p95_seconds"])
            for name, details in stages.items()
            if details["p95_seconds"] is not None
        ]
        if candidates:
            stage, seconds = max(candidates, key=lambda item: item[1])
            bottleneck = {
                "stage": stage,
                "basis": "largest_p95_seconds",
                "p95_seconds": seconds,
            }

    if valid_events == 0:
        reason = "no_valid_adb_input_timing_events"
    elif malformed_events:
        reason = "malformed_adb_input_timing_events"
    else:
        reason = ""
    return {
        "ok": complete,
        "complete": complete,
        "reason": reason,
        "event_count": valid_events,
        "malformed_event_count": malformed_events,
        "failure_count": outcome_counts["failed"],
        "failure_rate": (
            outcome_counts["failed"] / valid_events if valid_events else None
        ),
        "outcome_counts": outcome_counts,
        "command_counts": dict(sorted(command_counts.items())),
        "elapsed": _stage_summary(
            elapsed_values,
            failure_count=outcome_counts["failed"],
        ),
        "stages": stages,
        "bottleneck": bottleneck,
    }


def summarize_adb_input_timing_log(
    path: str | Path,
    *,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
) -> dict[str, Any]:
    log_path = Path(path)
    if not log_path.is_file():
        result = summarize_adb_input_timing_events((), malformed_lines=0, since=since, until=until)
        result["path"] = str(log_path)
        return result
    events: list[Any] = []
    malformed_lines = 0
    try:
        with log_path.open("rb") as stream:
            for raw_line in stream:
                if not raw_line.strip():
                    continue
                try:
                    events.append(json.loads(raw_line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    malformed_lines += 1
    except OSError as exc:
        result = summarize_adb_input_timing_events((), malformed_lines=0, since=since, until=until)
        result["reason"] = "device_health_log_read_error"
        result["read_error"] = f"{type(exc).__name__}: {exc}"
        result["path"] = str(log_path)
        return result
    result = summarize_adb_input_timing_events(
        events,
        malformed_lines=malformed_lines,
        since=since,
        until=until,
    )
    result["path"] = str(log_path)
    return result


def default_device_health_log_path(now: datetime | None = None) -> Path:
    current = now or datetime.now()
    return (
        get_settings().data_dir
        / "fanxiu"
        / "mumu-device-health"
        / f"device-health-{current:%Y%m%d}.jsonl"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize offline Fanxiu ADB input timings")
    parser.add_argument("path", nargs="?", type=Path, default=default_device_health_log_path())
    parser.add_argument("--since")
    parser.add_argument("--until")
    args = parser.parse_args(argv)
    result = summarize_adb_input_timing_log(args.path, since=args.since, until=args.until)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "default_device_health_log_path",
    "summarize_adb_input_timing_events",
    "summarize_adb_input_timing_log",
]
