from __future__ import annotations

from backend.core.fanxiu.instrumentation import xuanhuang
from backend.core.fanxiu.instrumentation.runtime_memory import (
    MumuProcessMemory,
)


def test_xuanhuang_snapshot_reads_loaded_remaining_count(
    monkeypatch,
):
    monkeypatch.setattr(
        xuanhuang,
        "_xuanhuang_data_fields",
        lambda _reader, _root: {"leftChangeTimes": 2.0},
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = xuanhuang._snapshot(
        memory,
        0x2000,
        root_cache_hit=True,
    )

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["counter_loaded"] is True
    assert result["remaining"] == 2
    assert result["protocol"].endswith(".leftChangeTimes")


def test_xuanhuang_data_requires_page_counter(monkeypatch):
    monkeypatch.setattr(
        xuanhuang,
        "manager_index_fields",
        lambda *_args, **_kwargs: {"inst": object()},
    )
    class FakeReader:
        @staticmethod
        def fields(_value):
            return {}

    try:
        xuanhuang._xuanhuang_data_fields(
            FakeReader(),
            0x2000,
        )
    except Exception as exc:
        assert "计数尚未加载" in str(exc)
    else:
        raise AssertionError("缺少 leftChangeTimes 时必须不可判定")
