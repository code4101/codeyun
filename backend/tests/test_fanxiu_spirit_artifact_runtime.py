from __future__ import annotations

from backend.core.fanxiu.instrumentation.spirit_artifact import (
    build_spirit_artifact_hall_from_runtime,
)


def _runtime_parts(group_count: int = 48) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    for group in range(1, group_count + 1):
        effects: list[dict[str, object]] = []
        if group == 1:
            effects = [
                {
                    "cleanse_id": 112002,
                    "value": 6700,
                    "base_value": 6500,
                    "add_value": 200,
                    "quality": 6,
                    "locked": True,
                    "name": "攻击",
                },
                {
                    "cleanse_id": 1_000_001,
                    "value": 2500,
                    "base_value": 2500,
                    "add_value": 0,
                    "quality": 7,
                    "locked": True,
                    "name": "灵器无双",
                },
                {
                    "cleanse_id": 9_999_999,
                    "value": 123,
                    "base_value": 100,
                    "add_value": 23,
                    "quality": 3,
                    "locked": False,
                    "name": "未归并效果",
                },
            ]
        parts.append(
            {
                "ware_id": (group - 1) // 6 + 1,
                "part": (group - 1) % 6 + 1,
                "item_id": str(24_000_000_000_000_000 + group),
                "base_id": 14_000_000 + group * 100 + 6,
                "grade": group,
                "realm": 2,
                "refine_num": 3,
                "is_break": group % 2 == 0,
                "effects": effects,
                "pending_effects": [],
            }
        )
    return parts


def test_runtime_snapshot_projects_current_slots_without_discarding_exact_fields():
    snapshot = build_spirit_artifact_hall_from_runtime(
        {"source": "test_runtime", "parts": _runtime_parts(), "pid": 123}
    )

    assert snapshot["runtime_complete"] is True
    assert snapshot["runtime_equipped_count"] == 48
    assert len(snapshot["artifacts"]) == 8
    assert sum(len(artifact["rows"]) for artifact in snapshot["artifacts"]) == 48

    row = snapshot["artifacts"][0]["rows"][0]
    assert row["rank"] == 1
    assert row["attack"] == "67%"
    assert row["artifact_peerless_1"] == 25
    assert row["runtime_base_id"] == 14_000_106
    assert row["runtime_item_id"] == "24000000000000001"
    assert row["runtime_ware_id"] == 1
    assert row["runtime_part"] == 1
    assert row["runtime_refine_num"] == 3
    assert row["runtime_is_break"] is False

    effects = {effect["cleanse_id"]: effect for effect in row["runtime_effects"]}
    assert effects[112002]["base_value"] == 6500
    assert effects[112002]["add_value"] == 200
    assert effects[112002]["locked"] is True
    assert effects[9_999_999]["official_name"] == "未归并效果"
    assert effects[9_999_999]["projection"] == ""
    assert row["runtime_pending_effects"] == []


def test_runtime_snapshot_projects_pending_refine_map_separately():
    parts = _runtime_parts()
    parts[0]["pending_effects"] = [
        {
            "cleanse_id": 112006,
            "value": 7200,
            "base_value": 7000,
            "add_value": 200,
            "quality": 7,
            "locked": False,
            "name": "攻击",
        }
    ]

    snapshot = build_spirit_artifact_hall_from_runtime({"parts": parts})
    row = snapshot["artifacts"][0]["rows"][0]

    assert row["runtime_effects"][0]["cleanse_id"] == 112002
    assert row["runtime_pending_effects"] == [
        {
            "cleanse_id": 112006,
            "value": 7200,
            "base_value": 7000,
            "add_value": 200,
            "quality": 7,
            "locked": False,
            "name": "攻击",
            "official_name": "攻击",
            "projection": "attack",
            "projection_base_value": 10000,
            "percent": "72%",
        }
    ]


def test_runtime_snapshot_rejects_incomplete_equipped_slots():
    parts = _runtime_parts()
    parts.pop()

    try:
        build_spirit_artifact_hall_from_runtime({"parts": parts})
    except RuntimeError as exc:
        assert "服务器装配引用不完整" in str(exc)
    else:
        raise AssertionError("incomplete runtime snapshot should fail")


def test_runtime_snapshot_naturally_includes_future_artifact_and_effects():
    parts = _runtime_parts(54)
    future_names = ("甲", "乙", "丙", "丁", "戊", "己")
    for index, part in enumerate(parts[48:]):
        part["artifact_name"] = "未来灵器"
        part["part_name"] = future_names[index]
    parts[48]["effects"] = [
        {"cleanse_id": 500_001, "value": 98_765, "name": "未来增伤"}
    ]

    snapshot = build_spirit_artifact_hall_from_runtime({"parts": parts})

    assert len(snapshot["artifacts"]) == 9
    future = snapshot["artifacts"][-1]
    assert future["name"] == "未来灵器"
    assert [row["part_name"] for row in future["rows"]] == list(future_names)
    assert future["rows"][0]["exclusive_stats"]["未来增伤"] == "98765"
