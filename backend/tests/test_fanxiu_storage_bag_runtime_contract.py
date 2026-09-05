from __future__ import annotations

import time

from backend.core.fanxiu.instrumentation import backpack_ui


class _Memory:
    pid = 123
    process_start_ticks = 456


class _Context:
    def __init__(self) -> None:
        self.memory = _Memory()
        self.timings: dict[str, float] = {}


def test_backpack_snapshot_exposes_observation_time_without_affecting_fingerprint(
    monkeypatch,
) -> None:
    context = _Context()
    monkeypatch.setattr(
        backpack_ui,
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

