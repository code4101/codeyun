from __future__ import annotations

from backend.core.fanxiu.instrumentation.seat_runtime import room_roster_facts


class _Reader:
    def fields(self, value):
        return value if isinstance(value, dict) else {}

    def dictionary_fields(self, value):
        return value if isinstance(value, dict) else {}

    def list_items(self, value):
        return list(value), len(value)

    def long(self, value):
        return value if isinstance(value, int) else None


def test_room_roster_normalizes_loaded_runtime_seats() -> None:
    result = room_roster_facts(
        _Reader(),
        {
            "roomInfoDic": {
                15: {
                    "roomId": 15,
                    "roomVO": {"id": 15, "left": 0},
                    "seats": [
                        {
                            "id": 7,
                            "preOwnerRoleId": 99,
                            "seatOwner": {
                                "roleId": 101,
                                "name": "目标",
                                "faze": 0,
                                "battleScore": 800,
                                "attributes": {2001: 321},
                                "serverId": 22001,
                                "protectEndTime": 0,
                            },
                        }
                    ],
                }
            }
        },
        room_id=15,
        room_summaries=[{"room_id": 15, "available_count": 0}],
        evidence={"order_key": [1.0]},
    )

    assert result["available"] is True
    assert result["complete"] is True
    assert result["declared_count"] == 1
    assert result["seats"][0]["owner"]["role_id"] == 101
    assert result["seats"][0]["owner"]["battle_score"] == 800
    assert result["seats"][0]["owner"]["attack_value"] == 321
    assert result["seats"][0]["owner"]["attack_source"] == "attributes[2001]"


def test_room_roster_reads_direct_attack_field_when_present() -> None:
    result = room_roster_facts(
        _Reader(),
        {
            "roomInfoDic": {
                15: {
                    "roomVO": {"id": 15, "left": 0},
                    "seats": [
                        {
                            "id": 7,
                            "seatOwner": {
                                "roleId": 101,
                                "name": "目标",
                                "battleScore": 800,
                                "attackValue": 456,
                            },
                        }
                    ],
                }
            }
        },
        room_id=15,
        room_summaries=[{"room_id": 15, "available_count": 0}],
        evidence={"order_key": [1.0]},
    )

    owner = result["seats"][0]["owner"]
    assert owner["attack_value"] == 456
    assert owner["attack_source"] == "attackValue"


def test_room_roster_does_not_estimate_attack_from_battle_score() -> None:
    result = room_roster_facts(
        _Reader(),
        {
            "roomInfoDic": {
                15: {
                    "roomVO": {"id": 15, "left": 0},
                    "seats": [
                        {
                            "id": 7,
                            "seatOwner": {
                                "roleId": 101,
                                "name": "目标",
                                "battleScore": 800,
                            },
                        }
                    ],
                }
            }
        },
        room_id=15,
        room_summaries=[{"room_id": 15, "available_count": 0}],
        evidence={"order_key": [1.0]},
    )

    owner = result["seats"][0]["owner"]
    assert owner["attack_value"] is None
    assert owner["attack_source"] is None


def test_room_roster_does_not_treat_cleared_full_room_as_empty() -> None:
    result = room_roster_facts(
        _Reader(),
        {
            "roomInfoDic": {
                15: {
                    "roomId": 15,
                    "roomVO": {"id": 15, "left": 0},
                    "seats": [],
                }
            }
        },
        room_id=15,
        room_summaries=[{"room_id": 15, "available_count": 0}],
        evidence={"order_key": [1.0]},
    )

    assert result["available"] is False
    assert result["complete"] is False
    assert result["reason"] == "room_roster_not_loaded"
    assert result["evidence"]["order_key"] == [1.0]
