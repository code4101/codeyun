from __future__ import annotations

from backend.core.fanxiu.instrumentation.lingquan import (
    _phase_and_remaining,
)


def test_lingquan_runtime_phase_and_countdown():
    common = {
        "start_time_ms": 1_000,
        "prepare_ms": 3_000,
        "question_ms": 40_000,
        "answer_ms": 3_000,
    }

    assert _phase_and_remaining(now_ms=2_000, **common) == (
        "prepare",
        2_000,
    )
    assert _phase_and_remaining(now_ms=10_000, **common) == (
        "question",
        34_000,
    )
    assert _phase_and_remaining(now_ms=45_000, **common) == (
        "answer",
        2_000,
    )
    assert _phase_and_remaining(now_ms=48_000, **common) == (
        "closed",
        0,
    )
