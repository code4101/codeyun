from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.core.fanxiu.activity import peakrace_business_snapshot


TZ = ZoneInfo("Asia/Shanghai")
IDENTITY = {"pid": 7300, "process_start_ticks": 9100}


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _schedule(*, available: bool = True) -> dict:
    return {
        "available": available,
        "complete": True,
        "source_kind": "worldline_activity_runtime_memory",
        "captured_at": "2026-09-01T05:01:00+08:00",
        "evidence": dict(IDENTITY),
        "items": [
            {
                "id": 32620001000001,
                "activityId": 32620001,
                "activityType": 79,
                "baseId": 170000,
                "startTime": _ms("2026-09-01T05:00:00+08:00"),
                "endTime": _ms("2026-09-06T22:00:05+08:00"),
                "closePanelTime": _ms("2026-09-06T23:59:59+08:00"),
            }
        ],
    }


def _peakrace(*, complete: bool = True) -> dict:
    return {
        "available": True,
        "complete": complete,
        "source_kind": "peakrace_runtime_memory",
        "captured_at": "2026-09-01T05:01:01+08:00",
        "evidence": dict(IDENTITY),
        "self_rank": 12 if complete else None,
        "current_round": 1 if complete else None,
        "activity_groups": (
            [{"activity_id": 1620100, "group": 2}] if complete else []
        ),
        "guesses": [],
        "worship_daily_times": 0 if complete else None,
    }


def test_composite_does_not_claim_open_observation_complete_without_child_and_ranks() -> None:
    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
    )

    assert result["ok"] is True
    assert result["available"] is True
    assert result["complete"] is False
    assert result["sources"]["identity_coherent"] is True
    assert result["state"]["variant"] == "32620001"
    assert result["state"]["stage"]["activity_id"] == 1620100
    assert result["state"]["qualification"] == "qualified"
    assert result["state"]["stage"]["schedule_phase"] == "unloaded"
    assert "current_stage_runtime_unobserved" in result["state"]["blockers"]
    assert "current_stage_rank_incomplete" in result["state"]["blockers"]
    assert "total_rank_incomplete" in result["state"]["blockers"]
    assert result["state"]["guess"]["status"] == "policy_unknown"
    assert result["state"]["guess"]["automation_allowed"] is False
    assert result["state"]["reward"]["automation_allowed"] is False
    assert result["state"]["worship"]["automation_allowed"] is False


def test_composite_rejects_snapshots_from_different_processes() -> None:
    peakrace = _peakrace()
    peakrace["evidence"] = {"pid": 7301, "process_start_ticks": 9200}

    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        peakrace,
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
    )

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["error_code"] == "source_identity_mismatch"
    assert result["state"] is None
    assert result["sources"]["identity_coherent"] is False


def test_prepare_can_be_observation_complete_before_self_data_loads() -> None:
    peakrace = _peakrace(complete=False)
    peakrace.update(error_code="data_not_loaded", reason="not naturally loaded")

    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        peakrace,
        now=datetime(2026, 9, 1, 2, 0, tzinfo=TZ),
    )

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["state"]["phase"] == "prepare"
    assert "peakrace_self_data_incomplete" not in result["state"]["blockers"]
    assert result["sources"]["peakrace_runtime"]["complete"] is False


def test_composite_requires_identity_on_two_available_runtime_sources() -> None:
    peakrace = _peakrace()
    peakrace.pop("evidence")

    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        peakrace,
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
    )

    assert result["error_code"] == "source_identity_missing"
    assert result["state"] is None


def test_read_entry_calls_only_the_two_readers_and_composes(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        peakrace_business_snapshot,
        "read_fanxiu_activity_runtime_schedule",
        lambda **kwargs: calls.append(("schedule", kwargs)) or _schedule(),
    )
    monkeypatch.setattr(
        peakrace_business_snapshot,
        "read_peakrace_runtime_snapshot",
        lambda **kwargs: calls.append(("peakrace", kwargs)) or _peakrace(),
    )

    result = peakrace_business_snapshot.read_peakrace_business_state_snapshot(
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
        allow_schedule_discovery=False,
        force_refresh=True,
    )

    assert result["ok"] is True
    assert calls == [
        ("schedule", {"allow_discovery": False, "force_refresh": True}),
        ("peakrace", {"force_refresh": True}),
    ]


def test_naive_business_time_fails_closed() -> None:
    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10),
    )

    assert result["error_code"] == "invalid_business_time"
    assert result["state"] is None


def test_unexpected_projection_failure_is_returned_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(
        peakrace_business_snapshot,
        "project_peakrace_business_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
    )

    assert result["error_code"] == "projection_failed"
    assert result["reason"] == "RuntimeError: boom"
    assert result["state"] is None


def test_composite_rejects_rank_snapshot_from_wrong_process_or_activity() -> None:
    rank = {
        "available": True,
        "complete": True,
        "rank_activity_id": 1620100,
        "captured_at": "2026-09-01T05:01:02+08:00",
        "evidence": {"pid": 7301, "process_start_ticks": 9200},
    }
    wrong_process = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
        rank_snapshots={1620100: rank},
    )
    assert wrong_process["error_code"] == "rank_source_identity_mismatch"
    assert wrong_process["state"] is None

    rank["evidence"] = dict(IDENTITY)
    wrong_activity = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
        rank_snapshots={624001: rank},
    )
    assert wrong_activity["error_code"] == "rank_activity_mismatch"


def test_composite_accepts_same_process_typed_rank_snapshot() -> None:
    rank = {
        "available": True,
        "complete": True,
        "rank_activity_id": 624001,
        "captured_at": "2026-09-01T05:01:02+08:00",
        "evidence": dict(IDENTITY),
        "rank_list_size": 64,
        "self_ranking": {"rank": 12, "score": 99},
    }
    result = peakrace_business_snapshot.compose_peakrace_business_state_snapshot(
        _schedule(),
        _peakrace(),
        now=datetime(2026, 9, 1, 5, 10, tzinfo=TZ),
        rank_snapshots={624001: rank},
    )

    assert result["ok"] is True
    assert result["sources"]["rank_snapshots"][0]["pid"] == IDENTITY["pid"]
