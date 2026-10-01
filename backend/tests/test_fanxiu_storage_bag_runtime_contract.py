from __future__ import annotations

import time

from backend.core.fanxiu.instrumentation import backpack_ui, ui_runtime_context
from backend.core.fanxiu.instrumentation.runtime_memory import FanxiuRuntimeMemoryError


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Context:
    def __init__(self) -> None:
        self.memory = _Memory()
        self.timings: dict[str, float] = {}
        self.cache_mode = "hot"


def test_backpack_snapshot_exposes_observation_time_without_affecting_fingerprint(
    monkeypatch,
) -> None:
    context = _Context()
    monkeypatch.setattr(
        ui_runtime_context,
        "acquire_ui_runtime_context",
        lambda _keys: context,
    )
    monkeypatch.setattr(
        backpack_ui,
        "_snapshot",
        lambda _context: {
            "ok": True,
            "complete": True,
            "source": "active_backpack_panel_item_info_list",
            "items": [],
            "performance": {},
            "evidence": {"pid": 123, "process_start_ticks": 456, "read_only": True},
        },
    )
    before = time.time()

    result = backpack_ui.read_backpack_ui_snapshot()

    after = time.time()
    assert before <= result["observed_at"] <= after
    assert result["captured_at_epoch"] == result["observed_at"]
    assert result["fingerprint"]


def test_backpack_decode_retry_uses_a_new_observation(monkeypatch) -> None:
    contexts = []

    def acquire(_keys):
        context = _Context()
        contexts.append(context)
        return context

    def decode(context):
        if len(contexts) == 1:
            raise FanxiuRuntimeMemoryError("transient list replacement", code="runtime_incomplete")
        return {"complete": True, "items": [], "performance": {},
                "evidence": {"pid": context.memory.pid}}

    monkeypatch.setattr(ui_runtime_context, "acquire_ui_runtime_context", acquire)
    monkeypatch.setattr(backpack_ui, "_snapshot", decode)
    result = backpack_ui.read_backpack_ui_snapshot()
    assert result["complete"] is True
    assert len(contexts) == 2
    assert contexts[0] is not contexts[1]


def test_backpack_persistent_failure_preserves_reason_and_budget(monkeypatch) -> None:
    contexts = []

    def acquire(_keys):
        context = _Context()
        contexts.append(context)
        return context

    def decode(_context):
        raise FanxiuRuntimeMemoryError("invalid item identity", code="runtime_incomplete")

    monkeypatch.setattr(ui_runtime_context, "acquire_ui_runtime_context", acquire)
    monkeypatch.setattr(backpack_ui, "_snapshot", decode)
    result = backpack_ui.read_backpack_ui_snapshot()
    assert result["complete"] is False
    assert result["reason"] == "invalid item identity"
    assert len(contexts) == 2
