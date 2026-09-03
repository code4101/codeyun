from __future__ import annotations

import json

import pytest

from backend.core.fanxiu.client.action_trace_timing import (
    summarize_action_trace_events,
    summarize_action_trace_log,
)


def _event(at: str, task: str, scene: int, *, phase: str = "go_scene"):
    return {
        "time": at,
        "kind": "click",
        "image_number": scene,
        "runtime_task": task,
        "phase": phase,
        "action": {"label": f"click #{scene}"},
    }


def test_action_trace_summary_splits_sessions_and_ranks_real_action_gaps():
    result = summarize_action_trace_events(
        [
            _event("2026-09-01T05:00:00", "助手", 34),
            _event("2026-09-01T05:00:05", "助手", 69),
            _event("2026-09-01T05:00:35", "助手", 204),
            _event("2026-09-01T05:01:00", "报名", 34),
            _event("2026-09-01T05:01:04", "报名", 69),
            _event("2026-09-01T06:00:00", "助手", 34),
        ],
        session_break_seconds=300,
        top_n=2,
    )

    assert result["ok"] is True
    assert result["event_count"] == 6
    assert result["session_count"] == 3
    assert result["tasks"]["助手"] == {
        "session_count": 2,
        "action_count": 4,
        "action_span_seconds": 35.0,
        "max_action_gap_seconds": 30.0,
    }
    assert result["top_action_gaps"][0]["gap_seconds"] == 30.0
    assert result["top_action_gaps"][0]["from_scene"] == 69
    assert result["top_action_gaps"][0]["to_scene"] == 204
    assert result["top_action_gaps"][0]["from_label"] == "click #69"
    assert result["top_action_gaps"][0]["to_label"] == "click #204"


def test_action_trace_summary_filters_window_and_fails_closed_on_malformed():
    result = summarize_action_trace_events(
        [
            _event("2026-09-01T04:59:59", "早", 34),
            _event("2026-09-01T05:00:01", "窗口", 34),
            {"time": "bad", "runtime_task": "窗口"},
        ],
        since="2026-09-01T05:00:00",
        until="2026-09-01T05:01:00",
    )

    assert result["ok"] is False
    assert result["event_count"] == 1
    assert result["malformed_event_count"] == 1
    assert result["reason"] == "malformed_action_trace_events"


def test_action_trace_summary_accepts_timezone_aware_bounds_for_local_wall_clock_events():
    result = summarize_action_trace_events(
        [
            _event("2026-09-01T04:59:59", "早", 34),
            _event("2026-09-01T05:00:01", "窗口", 34),
            _event("2026-09-01T05:01:01", "晚", 34),
        ],
        since="2026-08-31T21:00:00+00:00",
        until="2026-09-01T05:01:00+08:00",
    )

    assert result["ok"] is True
    assert result["event_count"] == 1
    assert result["malformed_event_count"] == 0
    assert result["sessions"][0]["started_at"] == "2026-09-01T05:00:01"


def test_action_trace_summary_keeps_unattributed_actions_in_separate_sessions():
    unattributed = _event("2026-09-01T05:00:02", "", 69)
    result = summarize_action_trace_events(
        [
            _event("2026-09-01T05:00:01", "报名", 34),
            unattributed,
            _event("2026-09-01T05:00:03", "报名", 69),
        ]
    )

    assert result["ok"] is True
    assert result["event_count"] == 3
    assert result["malformed_event_count"] == 0
    assert result["unattributed_event_count"] == 1
    assert result["session_count"] == 3
    assert result["tasks"]["<unattributed>"]["action_count"] == 1


def test_action_trace_log_reports_bad_json(tmp_path):
    path = tmp_path / "index.jsonl"
    path.write_text(
        json.dumps(_event("2026-09-01T05:00:01", "窗口", 34), ensure_ascii=False)
        + "\n{bad-json\n",
        encoding="utf-8",
    )

    result = summarize_action_trace_log(path)

    assert result["ok"] is False
    assert result["event_count"] == 1
    assert result["malformed_event_count"] == 1
    assert result["path"] == str(path)


def test_action_trace_summary_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="since must not be later than until"):
        summarize_action_trace_events(
            [],
            since="2026-09-01T06:00:00",
            until="2026-09-01T05:00:00",
        )
    with pytest.raises(ValueError, match="session_break_seconds must be positive"):
        summarize_action_trace_events([], session_break_seconds=0)
