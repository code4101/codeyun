from backend.core.fanxiu.instrumentation import alchemy
from backend.core.fanxiu.instrumentation.alchemy import classify_alchemy_transition
from backend.core.fanxiu.instrumentation.runtime_memory import (
    FanxiuRuntimeMemoryError,
)


def _snapshot(*, active: bool, proficiency: int, count: int = 0) -> dict:
    return {
        "complete": True,
        "active": active,
        "proficiency": proficiency,
        "total_operation_count": count,
        "evidence": {"pid": 7, "process_start_ticks": 11},
    }


def test_classify_alchemy_start_requires_exact_requested_count() -> None:
    result = classify_alchemy_transition(
        _snapshot(active=False, proficiency=100),
        _snapshot(active=True, proficiency=100, count=3),
        expected_count=3,
    )
    assert result == {
        "ok": True,
        "kind": "started",
        "actual_count": 3,
        "expected_count": 3,
    }

    mismatch = classify_alchemy_transition(
        _snapshot(active=False, proficiency=100),
        _snapshot(active=True, proficiency=100, count=2),
        expected_count=3,
    )
    assert mismatch["ok"] is False
    assert mismatch["kind"] == "count_mismatch"


def test_classify_alchemy_finish_uses_non_regressing_proficiency() -> None:
    result = classify_alchemy_transition(
        _snapshot(active=True, proficiency=100, count=3),
        _snapshot(active=False, proficiency=130),
    )
    assert result["ok"] is True
    assert result["kind"] == "finished"
    assert result["proficiency_delta"] == 30

    regressed = classify_alchemy_transition(
        _snapshot(active=True, proficiency=100, count=3),
        _snapshot(active=False, proficiency=90),
    )
    assert regressed["ok"] is False
    assert regressed["kind"] == "counter_regressed"


def test_classify_alchemy_transition_rejects_process_replacement() -> None:
    after = _snapshot(active=True, proficiency=100, count=1)
    after["evidence"]["process_start_ticks"] = 12
    result = classify_alchemy_transition(
        _snapshot(active=False, proficiency=100),
        after,
        expected_count=1,
    )
    assert result["ok"] is False
    assert result["kind"] == "process_changed"


def test_read_alchemy_snapshot_preserves_data_not_loaded(monkeypatch) -> None:
    memory = type("Memory", (), {"pid": 17, "process_start_ticks": 23})()

    class MemoryFactory:
        @staticmethod
        def discover_cached(*, max_age_seconds):
            assert max_age_seconds is None
            return memory

    def resolve(_memory, *, allow_diagnostic_discovery):
        assert _memory is memory
        assert allow_diagnostic_discovery is False
        raise FanxiuRuntimeMemoryError(
            "炼丹数据尚未下发",
            code="data_not_loaded",
        )

    monkeypatch.setattr(alchemy, "MumuProcessMemory", MemoryFactory)
    monkeypatch.setattr(alchemy, "_resolve_medicial_root", resolve)

    result = alchemy.read_alchemy_snapshot()

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["error_code"] == "data_not_loaded"
    assert result["load_state"] == "data_not_loaded"
    assert result["evidence"] == {
        "pid": 17,
        "process_start_ticks": 23,
        "read_only": True,
    }


def test_read_alchemy_snapshot_does_not_mask_unexpected_failure(monkeypatch) -> None:
    class MemoryFactory:
        @staticmethod
        def discover_cached(*, max_age_seconds):
            raise RuntimeError("probe exploded")

    monkeypatch.setattr(alchemy, "MumuProcessMemory", MemoryFactory)

    result = alchemy.read_alchemy_snapshot()

    assert result["ok"] is False
    assert result["error_code"] is None
    assert result["load_state"] == "error"
    assert result["reason"] == "RuntimeError: probe exploded"
    assert result["evidence"]["read_only"] is True
