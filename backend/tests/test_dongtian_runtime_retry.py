"""Deterministic retry budget, map freshness and process ownership contracts."""
from types import SimpleNamespace

import pytest

from backend.core.fanxiu.instrumentation import dongtian
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


@pytest.mark.parametrize("changed_process", [False, True])
def test_retry_refreshes_maps_and_preserves_process(monkeypatch, changed_process):
    discoveries = []
    reads = []

    def discover(**kwargs):
        discoveries.append(kwargs)
        return SimpleNamespace(
            pid=2 if changed_process and len(discoveries) == 2 else 1,
            process_start_ticks=10,
        )

    def read(memory, force_refresh):
        reads.append(force_refresh)
        if not force_refresh:
            raise FanxiuRuntimeMemoryError("Lua table array 地址无效", code="memory_address_unmapped")
        return {"complete": True}

    monkeypatch.setattr(dongtian.MumuProcessMemory, "discover_cached", discover)
    if changed_process:
        with pytest.raises(FanxiuRuntimeMemoryError, match="进程身份已变化"):
            dongtian._read_dongtian_with_generation_retry(read)
        assert reads == [False]
    else:
        result, attempts, first_error = dongtian._read_dongtian_with_generation_retry(read)
        assert result == {"complete": True}
        assert attempts == 2
        assert first_error == "Lua table array 地址无效"
        assert reads == [False, True]
    assert discoveries == [{}, {"max_age_seconds": 0.0}]


def test_retry_stops_after_second_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dongtian.MumuProcessMemory, "discover_cached",
        lambda **kwargs: SimpleNamespace(pid=1, process_start_ticks=10),
    )

    def read(memory, force_refresh):
        calls.append(force_refresh)
        raise FanxiuRuntimeMemoryError("Runtime 内存地址越界")

    with pytest.raises(FanxiuRuntimeMemoryError) as error:
        dongtian._read_dongtian_with_generation_retry(read)
    assert calls == [False, True]
    assert error.value.dongtian_decode_attempt_count == 2


@pytest.mark.parametrize("identity_ok", [True, False])
def test_session_recovery_requires_identity_before_reread(monkeypatch, identity_ok):
    session = object.__new__(dongtian.DongtianSeatingRuntimeSession)
    observations = []
    monkeypatch.setattr(session, "_fresh_memory", lambda: None)

    def revalidate():
        observations.append("identity")
        return {"ok": identity_ok}

    def read(memory):
        observations.append("read")
        if len(observations) == 1:
            raise FanxiuRuntimeMemoryError("array unmapped", code="memory_address_unmapped")
        return 1

    monkeypatch.setattr(session, "revalidate_process_identity", revalidate)
    if identity_ok:
        assert session._read_snapshot(read) == 1
        assert observations == ["read", "identity", "read"]
    else:
        with pytest.raises(FanxiuRuntimeMemoryError, match="身份校验失败"):
            session._read_snapshot(read)
        assert observations == ["read", "identity"]
