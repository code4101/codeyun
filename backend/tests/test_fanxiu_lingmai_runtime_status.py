from __future__ import annotations


def test_lingmai_daily_status_prefers_complete_runtime_snapshot(monkeypatch) -> None:
    from backend.core.fanxiu.data_annotation.tasks import lingmai
    from backend.core.fanxiu.instrumentation import lingmai as instrumentation

    snapshot = {
        "ok": True,
        "available": True,
        "complete": True,
        "completed": False,
        "remaining_milliseconds": 3_600_000,
        "source": "runtime_memory",
        "protocol": "UnionVenisMgr.Model.data",
    }
    monkeypatch.setattr(instrumentation, "read_lingmai_snapshot", lambda: snapshot)

    result = lingmai.refresh_lingmai_daily_status()

    assert result is snapshot


def test_lingmai_daily_status_preserves_runtime_unknown_without_packet_fallback(
    monkeypatch,
) -> None:
    from backend.core.fanxiu.data_annotation.tasks import lingmai
    from backend.core.fanxiu.instrumentation import lingmai as instrumentation

    execution_status = {
        "ok": False,
        "available": False,
        "complete": False,
        "reason": "manager_not_loaded",
    }
    monkeypatch.setattr(instrumentation, "read_lingmai_snapshot", lambda: execution_status)

    result = lingmai.refresh_lingmai_daily_status(wait_seconds=0)

    assert result is execution_status
    assert result["available"] is False
    assert result["reason"] == "manager_not_loaded"


def test_lingmai_588_runtime_state_uses_replay_transaction_before_scene() -> None:
    from backend.core.fanxiu.data_annotation.tasks.lingmai_execution import (
        LingmaiTaskMixin,
    )

    common = {
        "available": True,
        "complete": True,
        "self_seat_facts": {"available": True, "seated": False},
    }

    assert LingmaiTaskMixin._daily_lingmai_588_runtime_state(
        {
            **common,
            "battle_replay": {"available": True, "pending": True},
        }
    ) == "battle_pending"
    assert LingmaiTaskMixin._daily_lingmai_588_runtime_state(
        {
            **common,
            "battle_replay": {"available": True, "pending": False},
        }
    ) == "idle_unseated"


def test_lingmai_588_runtime_state_accepts_seated_only_after_replay_clears() -> None:
    from backend.core.fanxiu.data_annotation.tasks.lingmai_execution import (
        LingmaiTaskMixin,
    )

    assert LingmaiTaskMixin._daily_lingmai_588_runtime_state(
        {
            "available": True,
            "complete": True,
            "battle_replay": {"available": True, "pending": False},
            "self_seat_facts": {"available": True, "seated": True},
        }
    ) == "stable_seated"
    assert LingmaiTaskMixin._daily_lingmai_588_runtime_state(
        {
            "available": True,
            "complete": True,
            "battle_replay": {"available": False, "pending": None},
            "self_seat_facts": {"available": True, "seated": True},
        }
    ) == "unknown"
