from __future__ import annotations

import pytest

from backend.core.fanxiu.runtime_gui.mail import (
    MailVisualObservation,
    align_mail_window,
    build_mail_visual_observations,
    diagnose_mail_window,
    mail_snapshot_fingerprint,
    mail_snapshot_structure_fingerprint,
    mail_window_geometry_from_asset,
    stable_complete_mail_snapshots,
)
def _runtime_items() -> list[dict]:
    return [
        {
            "runtime_index": 0,
            "id": "a",
            "title": "资源领取通知",
            "create_time_text": "2026年08月04日22:00",
        },
        {
            "runtime_index": 1,
            "id": "b",
            "title": "装备重铸所得",
            "create_time_text": "2026年08月04日21:49",
        },
        {
            "runtime_index": 2,
            "id": "c",
            "title": "奇袭魔界奖励",
            "create_time_text": "2026年08月04日21:30",
        },
        {
            "runtime_index": 3,
            "id": "d",
            "title": "宗门镇邪活动奖励",
            "create_time_text": "2026年08月04日21:05",
        },
        {
            "runtime_index": 4,
            "id": "e",
            "title": "仙财福礼",
            "create_time_text": "2026年08月04日20:00",
        },
    ]


def test_geometry_uses_first_and_second_mail_centers_as_pitch() -> None:
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {
                "kind": "shape",
                "title": "第1封",
                "x": 0.1,
                "y": 0.2145833333,
                "w": 0.6,
                "h": 0.0822916667,
            },
            {
                "kind": "shape",
                "title": "第2封",
                "x": 0.1,
                "y": 0.3364583333,
                "w": 0.6,
                "h": 0.0760416667,
            },
            {
                "kind": "shape",
                "title": "邮件模板",
                "x": 0.2,
                "y": 0.4489583333,
                "w": 0.6,
                "h": 0.0916666667,
                "children": [
                    {
                        "kind": "shape",
                        "title": "标题",
                        "x": 0.2,
                        "y": 0.453125,
                        "w": 0.4,
                        "h": 0.0395833333,
                    },
                    {
                        "kind": "shape",
                        "title": "时间",
                        "x": 0.2,
                        "y": 0.4989583333,
                        "w": 0.4,
                        "h": 0.0322916667,
                    },
                ],
            },
            {
                "kind": "shape",
                "title": "邮件清单2",
                "x": 0.1,
                "y": 0.3364583333,
                "w": 0.8,
                "h": 0.4104166667,
            },
        ],
    }

    geometry = mail_window_geometry_from_asset(image)

    assert geometry.first_center_y == pytest.approx(409.16666664)
    assert geometry.second_center_y == pytest.approx(599.16666664)
    assert geometry.row_pitch == pytest.approx(190.0)
    assert round(geometry.title_center_offset, 1) == -35.0
    assert round(geometry.time_center_offset, 1) == 32.5
    assert geometry.visible_slot_indices() == (0, 1, 2, 3)


def test_alignment_infers_polluted_first_row_from_three_fuzzy_neighbors() -> None:
    observations = [
        MailVisualObservation(
            0, 409, ("世界公告乱字",), (), trusted=False, reliability=0.1
        ),
        MailVisualObservation(1, 599, ("奇袭魔界奖劢",), ("2026年08月04日21:30",)),
        MailVisualObservation(2, 789, ("宗门镇邪活动奖",), ("08月04日21:05",)),
        MailVisualObservation(3, 979, ("仙财福礼",), ("2026年08月04日20:00",)),
    ]

    result = align_mail_window(_runtime_items(), observations, visible_slots=range(4))

    assert result.status == "aligned"
    assert result.runtime_offset == 1
    assert result.anchor_count == 3
    assert [item["mail_id"] for item in result.mappings] == ["b", "c", "d", "e"]
    assert result.mappings[0]["observed"] is True
    assert result.mappings[0]["inferred"] is True


def test_alignment_rejects_one_non_unique_anchor() -> None:
    observations = [MailVisualObservation(2, 789, ("仙财福礼",), ())]

    result = align_mail_window(_runtime_items(), observations, visible_slots=range(4))

    assert result.status == "insufficient_evidence"
    assert result.mappings == ()


def test_alignment_rejects_repeated_sequence_without_score_margin() -> None:
    rows = [
        {
            "runtime_index": index,
            "id": str(index),
            "title": "分红发放",
            "create_time_text": "2026年08月04日13:07",
        }
        for index in range(6)
    ]
    observations = [
        MailVisualObservation(1, 599, ("分红发放",), ("2026年08月04日13:07",)),
        MailVisualObservation(2, 789, ("分红发放",), ("2026年08月04日13:07",)),
    ]

    result = align_mail_window(rows, observations, visible_slots=range(4))

    assert result.status == "ambiguous"
    assert result.mappings == ()
    assert len(result.hypotheses) == len(result.competitive_offsets)
    assert {item.runtime_offset for item in result.hypotheses} == set(result.competitive_offsets)


def test_repeated_title_without_time_is_not_an_exact_identity_anchor() -> None:
    rows = [
        {
            "runtime_index": index,
            "id": str(index),
            "title": "香车馈赠",
            "create_time_text": time_text,
        }
        for index, time_text in enumerate(
            ["2026年08月18日05:24", "2026年08月18日05:22", "2026年08月18日05:21"]
        )
    ]
    observations = [
        MailVisualObservation(0, 0, ("香车馈赠",), ()),
        MailVisualObservation(1, 0, ("香车馈赠",), ()),
    ]

    result = align_mail_window(rows, observations, visible_slots=range(2))

    assert result.status == "ambiguous"
    assert all(item.exact_anchor_count == 0 for item in result.hypotheses)


def test_repeated_title_uses_time_to_select_the_ordered_runtime_fragment() -> None:
    rows = [
        {
            "runtime_index": index,
            "id": str(index),
            "title": "香车馈赠",
            "create_time_text": time_text,
        }
        for index, time_text in enumerate(
            [
                "2026年08月18日07:42",
                "2026年08月18日05:24",
                "2026年08月18日05:22",
                "2026年08月18日05:21",
                "2026年08月18日05:21",
            ]
        )
    ]
    observations = [
        MailVisualObservation(0, 0, ("香车馈赠",), ("2026年08月18日05:24",)),
        MailVisualObservation(1, 0, ("香车馈赠",), ("2026年08月18日05:22",)),
        MailVisualObservation(2, 0, ("香车馈赠",), ("2026年08月18日05:21",)),
    ]

    result = align_mail_window(rows, observations, visible_slots=range(3))

    assert result.status == "aligned"
    assert result.runtime_offset == 1
    assert [item["mail_id"] for item in result.mappings] == ["1", "2", "3"]


def test_alignment_prefers_three_exact_ordered_anchors_over_near_score_repeated_titles() -> None:
    rows = [
        {
            "runtime_index": 0,
            "id": "gift",
            "title": "仙财福礼",
            "create_time_text": "2026年08月18日12:00",
        },
        *[
            {
                "runtime_index": index,
                "id": f"locked-car-{index}",
                "title": "香车馈赠",
                "create_time_text": "2026年08月18日07:42",
            }
            for index in range(1, 4)
        ],
        *[
            {
                "runtime_index": index,
                "id": f"old-{index}",
                "title": "香车馈赠",
                "create_time_text": "2026年08月18日05:21",
            }
            for index in range(4, 9)
        ],
    ]
    observations = [
        MailVisualObservation(
            slot_index=index,
            center_y=0,
            title_candidates=("香车馈赠",),
            time_candidates=("2026年08月18日07:42",),
        )
        for index in range(1, 4)
    ]

    result = align_mail_window(
        rows,
        observations,
        visible_slots=range(4),
        expected_runtime_offset=0,
    )

    assert result.status == "aligned"
    assert result.runtime_offset == 0
    assert result.exact_anchor_count == 3
    assert [item["mail_id"] for item in result.mappings] == [
        "gift",
        "locked-car-1",
        "locked-car-2",
        "locked-car-3",
    ]


def test_visual_mail_newer_than_snapshot_requires_refresh() -> None:
    observations = [
        MailVisualObservation(
            1,
            599,
            ("刚刚到达的新邮件",),
            ("2026年08月04日22:30",),
        )
    ]

    result = align_mail_window(_runtime_items(), observations)

    assert result.status == "snapshot_stale"
    assert result.stale_evidence[0]["visual_time"] == "202608042230"


def test_snapshot_fingerprint_tracks_order_and_action_state() -> None:
    rows = _runtime_items()
    original = mail_snapshot_fingerprint(rows)
    reversed_order = mail_snapshot_fingerprint(list(reversed(rows)))
    changed = [dict(item) for item in rows]
    changed[2]["reward_getted"] = True

    assert (
        original == reversed_order
    )  # runtime_index, not input container order, is authoritative
    assert original != mail_snapshot_fingerprint(changed)


def test_snapshot_structure_fingerprint_ignores_claim_state_but_tracks_structure() -> None:
    rows = _runtime_items()
    original = mail_snapshot_structure_fingerprint(rows)
    claimed = [dict(item) for item in rows]
    claimed[2]["reward_getted"] = True
    locked = [dict(item) for item in rows]
    locked[2]["locked"] = not bool(locked[2].get("locked"))
    arrived = [dict(item) for item in rows]
    arrived.append(
        {
            "runtime_index": len(arrived),
            "id": "new-mail",
            "title": "新邮件",
            "create_time_ms": 999,
            "locked": False,
            "reward_getted": False,
        }
    )

    assert original == mail_snapshot_structure_fingerprint(claimed)
    assert original != mail_snapshot_structure_fingerprint(locked)
    assert original != mail_snapshot_structure_fingerprint(arrived)


def test_consecutive_complete_snapshots_must_have_the_same_sequence() -> None:
    first_items = _runtime_items()
    first = {"complete": True, "decoded_count": len(first_items), "items": first_items}
    second = {
        "complete": True,
        "decoded_count": len(first_items),
        "items": [dict(item) for item in first_items],
    }

    assert stable_complete_mail_snapshots(first, second)["stable"] is True

    second["items"][0]["reward_getted"] = True
    changed = stable_complete_mail_snapshots(first, second)
    assert changed["stable"] is False
    assert "重新观察 #121" in changed["reason"]

    empty = {"complete": True, "decoded_count": 0, "items": []}
    assert stable_complete_mail_snapshots(empty, empty)["stable"] is True


def test_known_continuous_offset_accepts_one_exact_anchor_only() -> None:
    exact = MailVisualObservation(
        slot_index=1,
        center_y=0,
        title_candidates=("奇袭魔界奖励",),
        time_candidates=("2026年08月04日21:30",),
    )
    aligned = align_mail_window(
        _runtime_items(),
        [exact],
        visible_slots=range(4),
        min_anchor_count=1,
        expected_runtime_offset=1,
    )
    assert aligned.aligned is True
    assert aligned.runtime_offset == 1

    fuzzy = MailVisualObservation(
        slot_index=1,
        center_y=0,
        title_candidates=("奇袭魔界奖劢",),
    )
    rejected = align_mail_window(
        _runtime_items(),
        [fuzzy],
        visible_slots=range(4),
        min_anchor_count=1,
        expected_runtime_offset=1,
    )
    assert rejected.status == "insufficient_evidence"
    assert "精确 OCR 锚点" in rejected.reason


def test_expected_offset_still_rejects_visually_identical_neighbour_rows() -> None:
    rows = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "奖励请查收" if 1 <= index <= 3 else f"邮件{index}",
            "create_time_text": (
                "2026年08月16日15:14" if 1 <= index <= 3 else f"2026年08月16日14:{index:02d}"
            ),
        }
        for index in range(8)
    ]
    duplicate = MailVisualObservation(
        slot_index=1,
        center_y=0,
        title_candidates=("奖励请查收",),
        time_candidates=("2026年08月16日15:14",),
    )

    alignment = align_mail_window(
        rows,
        [duplicate],
        visible_slots=range(4),
        min_anchor_count=1,
        expected_runtime_offset=1,
    )

    assert alignment.status == "ambiguous"
    assert alignment.runtime_offset is None


def test_raw_ocr_fragments_are_bucketed_by_title_and_time_lattices() -> None:
    geometry = mail_window_geometry_from_asset(
        {
            "width": 900,
            "height": 1600,
            "shapes": [
                {"kind": "shape", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
                {
                    "kind": "shape",
                    "title": "第2封",
                    "x": 0,
                    "y": 0.31875,
                    "w": 1,
                    "h": 0.1,
                },
            ],
        }
    )
    fragments = [
        {"text": "公告污染", "y": 380, "h": 20},
        {"text": "奇袭魔界奖劢", "y": 570, "h": 20},
        {"text": "2026年08月04日21:30", "y": 610, "h": 20},
    ]

    observations = build_mail_visual_observations(
        fragments, geometry, visible_slots=range(4)
    )

    assert observations[0].slot_index == 0
    assert observations[0].trusted is False
    assert observations[1].slot_index == 1
    assert observations[1].title_candidates == ("奇袭魔界奖劢",)
    assert observations[1].time_candidates == ("2026年08月04日21:30",)


def test_visible_slots_exclude_row_partly_covered_by_mail_footer() -> None:
    geometry = mail_window_geometry_from_asset(
        {
            "width": 900,
            "height": 1600,
            "shapes": [
                {"kind": "shape", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
                {"kind": "shape", "title": "第2封", "x": 0, "y": 0.31875, "w": 1, "h": 0.1},
                {
                    "kind": "shape",
                    "title": "邮件清单2",
                    "x": 0,
                    "y": 0.2,
                    "w": 1,
                    # Slot 3 center is inside this box, but its lower half is not.
                    "h": 0.425,
                },
            ],
        }
    )

    assert geometry.visible_slot_indices() == (0, 1, 2)


def test_readonly_diagnosis_returns_auditable_slot_to_mail_mapping() -> None:
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "shape", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
            {"kind": "shape", "title": "第2封", "x": 0, "y": 0.31875, "w": 1, "h": 0.1},
            {
                "kind": "shape",
                "title": "邮件清单2",
                "x": 0,
                "y": 0.31875,
                "w": 1,
                "h": 0.5,
            },
        ],
    }
    snapshot = {
        "complete": True,
        "decoded_count": 5,
        "items": _runtime_items(),
    }
    fragments = [
        {"text": "奇袭魔界奖劢", "y": 570, "h": 20},
        {"text": "2026年08月04日21:30", "y": 590, "h": 20},
        {"text": "宗门镇邪活动奖", "y": 760, "h": 20},
        {"text": "2026年08月04日21:05", "y": 780, "h": 20},
    ]

    result = diagnose_mail_window(snapshot, image, fragments)

    assert result["ok"] is True
    assert result["visible_slots"] == [0, 1, 2, 3, 4]
    assert [item["mail_id"] for item in result["alignment"]["mappings"]] == [
        "b",
        "c",
        "d",
        "e",
    ]
