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

    runtime_status = {
        "ok": False,
        "available": False,
        "complete": False,
        "reason": "manager_not_loaded",
    }
    monkeypatch.setattr(instrumentation, "read_lingmai_snapshot", lambda: runtime_status)

    result = lingmai.refresh_lingmai_daily_status(wait_seconds=0)

    assert result is runtime_status
    assert result["available"] is False
    assert result["reason"] == "manager_not_loaded"
