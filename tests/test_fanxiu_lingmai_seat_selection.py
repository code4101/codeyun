from __future__ import annotations

from backend.core.fanxiu.data_annotation.tasks import daofa, lingmai


def test_unseated_lingmai_selection_uses_player_profile_snapshot(monkeypatch):
    monkeypatch.setattr(
        daofa,
        "current_player_battle_score",
        lambda: {
            "ok": True,
            "available": True,
            "role_id": 1001,
            "battle_score": 10_000,
        },
    )
    snapshot = {
        "self_profile": {
            "ok": False,
            "available": False,
            "reason": "self_profile_not_loaded",
        },
        "self_seat_facts": {
            "ok": True,
            "available": True,
            "seated": False,
            "seat": None,
        },
        "union_group_facts": {
            "ok": True,
            "available": True,
            "veins_group": 88,
        },
        "shenmai_roster": {
            "ok": True,
            "available": True,
            "complete": True,
            "room_id": 17,
            "available_count": 1,
            "seats": [],
        },
    }

    result = lingmai.read_and_select_lingmai_runtime_action(snapshot=snapshot)

    assert result["ok"] is True
    assert result["action"] == "occupy_empty"
    assert result["player_profile"]["role_id"] == 1001
    assert result["player_profile"]["source"] == "player_profile_snapshot"


def test_loaded_lingmai_runtime_profile_stays_authoritative(monkeypatch):
    def fail_if_called():
        raise AssertionError("player snapshot must not replace a loaded Runtime profile")

    monkeypatch.setattr(daofa, "current_player_battle_score", fail_if_called)
    snapshot = {
        "self_profile": {
            "ok": True,
            "available": True,
            "role_id": 1001,
            "battle_score": 10_000,
            "source": "runtime_memory",
        },
        "self_seat_facts": {
            "ok": True,
            "available": True,
            "seated": False,
            "seat": None,
        },
        "union_group_facts": {
            "ok": True,
            "available": True,
            "veins_group": 88,
        },
        "shenmai_roster": {
            "ok": True,
            "available": True,
            "complete": True,
            "room_id": 17,
            "available_count": 1,
            "seats": [],
        },
    }

    result = lingmai.read_and_select_lingmai_runtime_action(snapshot=snapshot)

    assert result["ok"] is True
    assert result["player_profile"]["source"] == "runtime_memory"
