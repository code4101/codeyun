from __future__ import annotations

import json

import pytest

from backend.core.fanxiu.client.adb_input_timing import (
    summarize_adb_input_timing_events,
    summarize_adb_input_timing_log,
)


def _event(
    *,
    observed_at: str,
    outcome: str = "success",
    elapsed: float = 1.0,
    ensure: float = 0.1,
    connect: float = 0.2,
    wm_size: float = 0.3,
    input_seconds: float = 0.4,
    fallback: float | None = None,
):
    stages = {"ensure": ensure}
    if fallback is not None:
        stages["manager_fallback"] = fallback
    attempt = {
        "serial": "127.0.0.1:7555",
        "attempt": 1,
        "outcome": "success" if outcome == "success" else "failed",
        "elapsed_seconds": elapsed,
        "stages_seconds": {
            "connect": connect,
            "wm_size": wm_size,
            "input": input_seconds,
        },
    }
    if attempt["outcome"] == "failed":
        attempt["failed_stage"] = "input"
    return {
        "time": observed_at,
        "event": "adb_input_timing",
        "command_kind": "input_tap",
        "timeout_seconds": 5,
        "outcome": outcome,
        "elapsed_seconds": elapsed,
        "stages_seconds": stages,
        "attempts": [attempt],
    }


def test_adb_input_timing_summary_aggregates_percentiles_failures_and_bottleneck():
    events = [
        _event(observed_at="2026-09-01T05:00:01", elapsed=1, input_seconds=1),
        _event(observed_at="2026-09-01T05:00:02", elapsed=2, input_seconds=2),
        _event(
            observed_at="2026-09-01T05:00:03",
            outcome="failed",
            elapsed=4,
            input_seconds=4,
        ),
        _event(
            observed_at="2026-09-01T05:00:04",
            outcome="manager_fallback",
            elapsed=8,
            input_seconds=1,
            fallback=8,
        ),
        {"time": "2026-09-01T05:00:00", "event": "health_check"},
    ]

    result = summarize_adb_input_timing_events(events)

    assert result["ok"] is True
    assert result["event_count"] == 4
    assert result["failure_count"] == 1
    assert result["failure_rate"] == 0.25
    assert result["elapsed"]["count"] == 4
    assert result["elapsed"]["p50_seconds"] == 3.0
    assert result["elapsed"]["p95_seconds"] == pytest.approx(7.4)
    assert result["elapsed"]["max_seconds"] == 8.0
    assert result["elapsed"]["failure_rate"] == 0.25
    assert result["stages"]["input"]["p95_seconds"] == pytest.approx(3.7)
    assert result["stages"]["input"]["failure_rate"] == 0.5
    assert result["stages"]["fallback"]["count"] == 1
    assert result["bottleneck"] == {
        "stage": "fallback",
        "basis": "largest_p95_seconds",
        "p95_seconds": 8.0,
    }


def test_adb_input_timing_summary_fails_closed_on_empty_or_malformed_samples():
    empty = summarize_adb_input_timing_events([])
    assert empty["ok"] is False
    assert empty["reason"] == "no_valid_adb_input_timing_events"
    assert empty["failure_rate"] is None
    assert empty["bottleneck"] is None

    malformed = summarize_adb_input_timing_events([
        _event(observed_at="2026-09-01T05:00:01"),
        {
            "time": "2026-09-01T05:00:02",
            "event": "adb_input_timing",
            "command_kind": "input_tap",
            "outcome": "success",
            "elapsed_seconds": -1,
            "stages_seconds": {"ensure": 0.1},
            "attempts": [],
        },
    ])
    assert malformed["ok"] is False
    assert malformed["event_count"] == 1
    assert malformed["malformed_event_count"] == 1
    assert malformed["reason"] == "malformed_adb_input_timing_events"
    assert malformed["bottleneck"] is None


def test_adb_input_timing_log_filters_window_and_rejects_bad_json(tmp_path):
    path = tmp_path / "device-health.jsonl"
    lines = [
        json.dumps(_event(observed_at="2026-09-01T04:59:59")),
        json.dumps(_event(observed_at="2026-09-01T05:00:30", input_seconds=2)),
        "{bad-json",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")

    result = summarize_adb_input_timing_log(
        path,
        since="2026-09-01T05:00:00",
        until="2026-09-01T05:01:00",
    )

    assert result["ok"] is False
    assert result["event_count"] == 1
    assert result["malformed_event_count"] == 1
    assert result["bottleneck"] is None


def test_adb_input_timing_summary_accepts_aware_bounds_for_local_wall_clock_events():
    result = summarize_adb_input_timing_events(
        [
            _event(observed_at="2026-09-01T04:59:59"),
            _event(observed_at="2026-09-01T05:00:30"),
            _event(observed_at="2026-09-01T05:01:01"),
        ],
        since="2026-08-31T21:00:00+00:00",
        until="2026-09-01T05:01:00+08:00",
    )

    assert result["ok"] is True
    assert result["event_count"] == 1
    assert result["malformed_event_count"] == 0


def test_adb_input_timing_summary_rejects_inverted_window():
    with pytest.raises(ValueError, match="since must not be later than until"):
        summarize_adb_input_timing_events(
            [],
            since="2026-09-01T06:00:00",
            until="2026-09-01T05:00:00",
        )
