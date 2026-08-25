from __future__ import annotations

from backend.core.fanxiu.instrumentation import role_progression
from backend.core.fanxiu.instrumentation.runtime_memory import MumuProcessMemory


def test_role_progression_snapshot_marks_full_experience(monkeypatch):
    monkeypatch.setattr(
        role_progression,
        "_role_progression_values",
        lambda _reader, _root: (240, 1_545_491_002, 1_543_965_623),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = role_progression._snapshot(
        memory,
        0x2000,
        root_cache_hit=True,
    )

    assert result["state"] == "realm_upgrade_ready"
    assert result["can_upgrade"] is True
    assert result["overflow_exp"] == 1_525_379
    assert result["level"] == 240


def test_role_progression_snapshot_keeps_experience_usable(monkeypatch):
    monkeypatch.setattr(
        role_progression,
        "_role_progression_values",
        lambda _reader, _root: (240, 1_000, 2_000),
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = role_progression._snapshot(
        memory,
        0x2000,
        root_cache_hit=False,
    )

    assert result["state"] == "experience_usable"
    assert result["can_upgrade"] is False
    assert result["overflow_exp"] == 0


def test_role_profile_snapshot_exposes_authoritative_comparison_fields(monkeypatch):
    monkeypatch.setattr(
        role_progression,
        "_role_profile_values",
        lambda _reader, _root: {
            "role_id": 24082878061086206,
            "name": "自己",
            "battle_score": 3.3e21,
            "faze": 0,
        },
    )
    memory = MumuProcessMemory(
        pid=123,
        process_start_ticks=456,
        adb_serial="test",
        regions=[],
    )

    result = role_progression._role_profile_snapshot(
        memory,
        0x2000,
        root_cache_hit=True,
    )

    assert result["available"] is True
    assert result["role_id"] == 24082878061086206
    assert result["battle_score"] == 3.3e21
    assert result["faze"] == 0
    assert result["source"] == "runtime_memory"
