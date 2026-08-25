from __future__ import annotations

import pytest
import threading
from types import SimpleNamespace

from backend.core.fanxiu.mail.visual_alignment import (
    MailVisualObservation,
    align_mail_window,
    build_mail_visual_observations,
    diagnose_mail_window,
    mail_snapshot_fingerprint,
    mail_snapshot_structure_fingerprint,
    mail_window_geometry_from_asset,
    stable_complete_mail_snapshots,
)
from backend.core.fanxiu.data_annotation.runner import (
    create_behavior_tree_runtime_runner,
)


def _return_generator(value):
    if False:
        yield None
    return value


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


def test_precise_claim_targets_only_include_current_unlocked_claim_policy() -> None:
    runner = create_behavior_tree_runtime_runner()
    snapshot = {
        "items": [
            {"id": "claim", "runtime_status": "unclaimed", "present_in_runtime": True, "locked": False, "action_policy": "claim"},
            {"id": "locked", "runtime_status": "unclaimed", "present_in_runtime": True, "locked": True, "action_policy": "claim"},
            {"id": "retain", "runtime_status": "unclaimed", "present_in_runtime": True, "locked": False, "action_policy": ""},
            {"id": "claimed", "runtime_status": "claimed", "present_in_runtime": True, "locked": False, "action_policy": "claim"},
            {"id": "absent", "runtime_status": "unclaimed", "present_in_runtime": False, "locked": False, "action_policy": "claim"},
        ]
    }

    assert [item["id"] for item in runner._precise_mail_claim_targets(snapshot)] == ["claim"]


def test_explicit_mail_ids_narrow_the_precise_claim_batch() -> None:
    runner = create_behavior_tree_runtime_runner()
    snapshot = {
        "items": [
            {
                "id": mail_id,
                "runtime_status": "unclaimed",
                "present_in_runtime": True,
                "locked": False,
                "action_policy": "claim",
            }
            for mail_id in ("maintenance", "medusa", "lingmai")
        ]
    }

    selected = runner._select_precise_mail_claim_targets(snapshot, {"medusa"})

    assert [item["id"] for item in selected] == ["medusa"]
    assert [
        item["id"]
        for item in runner._select_precise_mail_claim_targets(snapshot, set())
    ] == ["maintenance", "medusa", "lingmai"]


def test_visible_claim_target_is_planned_before_any_scroll() -> None:
    runner = create_behavior_tree_runtime_runner()
    mappings = [
        {"slot_index": 0, "runtime_index": 7, "mail_id": "retain"},
        {"slot_index": 1, "runtime_index": 8, "mail_id": "claim-now"},
        {"slot_index": 2, "runtime_index": 9, "mail_id": "later"},
    ]
    targets = [
        {"runtime_index": 8, "id": "claim-now", "action_policy": "claim"},
        {"runtime_index": 12, "id": "below-window", "action_policy": "claim"},
    ]

    plan = runner._plan_precise_mail_window_action(mappings, targets)

    assert plan["action"] == "claim"
    assert plan["mapping"]["mail_id"] == "claim-now"


def test_later_contiguous_anchors_infer_and_claim_occluded_first_row() -> None:
    runner = create_behavior_tree_runtime_runner()
    rows = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": title,
            "create_time_text": time_text,
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim",
        }
        for index, (title, time_text) in enumerate(
            [
                ("奇袭魔界奖励", "2026年08月18日13:00"),
                ("仙财福礼", "2026年08月18日12:00"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("玄荒古域奖励", "2026年08月18日07:30"),
            ]
        )
    ]
    observations = [
        MailVisualObservation(1, 0, ("仙财福礼",), ("2026年08月18日12:00",)),
        MailVisualObservation(2, 0, ("香车馈赠",), ("2026年08月18日07:42",)),
        MailVisualObservation(3, 0, ("玄荒古域奖励",), ("2026年08月18日07:30",)),
    ]

    alignment = align_mail_window(
        rows,
        observations,
        visible_slots=range(4),
        expected_runtime_offset=0,
    )
    plan = runner._plan_precise_mail_window_action(list(alignment.mappings), rows)

    assert alignment.status == "aligned"
    assert alignment.mappings[0]["mail_id"] == "mail-0"
    assert alignment.mappings[0]["observed"] is False
    assert plan["action"] == "claim"
    assert plan["mapping"]["slot_index"] == 0


def test_formal_first_screen_claim_calls_existing_claim_without_scroll(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "id": 121,
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "rect", "title": "第1封", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "第2封", "x": 0.08, "y": 0.31875, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "邮件清单2", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.55},
        ],
    }
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": title,
            "create_time_text": time_text,
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim",
        }
        for index, (title, time_text) in enumerate(
            [
                ("奇袭魔界奖励", "2026年08月18日13:00"),
                ("仙财福礼", "2026年08月18日12:00"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("香车馈赠", "2026年08月18日07:42"),
            ]
        )
    ]
    fragments = []
    for center_y, title, time_text in (
        (590, "仙财福礼", "2026年08月18日12:00"),
        (780, "香车馈赠", "2026年08月18日07:42"),
        (970, "香车馈赠", "2026年08月18日07:42"),
    ):
        fragments.extend(
            [
                {"text": title, "x": 250, "y": center_y - 25, "w": 180, "h": 20},
                {"text": time_text, "x": 250, "y": center_y + 5, "w": 280, "h": 20},
            ]
        )

    class FakeRuntime:
        scroll_calls = 0

        def cur_frame(self, *, update=False):
            assert update is True
            return "frame"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return fragments

        def scroll_shape_content(self, *_args, **_kwargs):
            self.scroll_calls += 1
            raise AssertionError("首屏领取前禁止 scroll")

        def drag_shape_content(self, *_args, **_kwargs):
            raise AssertionError("首屏领取前禁止 drag")

    opened: list[str] = []

    def fake_claim(_runtime, mail, **kwargs):
        opened.append(mail.title)
        assert kwargs == {"delete_after_reward": False, "require_claim": True}
        if False:
            yield None
        return SimpleNamespace(policy="claim", wait_result="list", visual_confirmed=True)

    monkeypatch.setattr(runner, "_claim_runtime_mail_row", fake_claim)
    from pyxllib.autogui import View

    view121 = View(image)
    list_shape = view121.get_shape("邮件清单2")
    assert list_shape is not None
    runtime = FakeRuntime()
    execution = runner._execute_first_screen_runtime_claim(
        {},
        threading.Event(),
        runtime=runtime,
        image121=image,
        view121=view121,
        list_shape=list_shape,
        geometry=mail_window_geometry_from_asset(image),
        snapshot={"complete": True, "decoded_count": len(items), "items": items},
    )
    with pytest.raises(StopIteration) as finished:
        while True:
            next(execution)

    assert finished.value.value == "success"
    assert opened == ["奇袭魔界奖励"]
    assert runtime.scroll_calls == 0


def test_ordered_batch_reocr_and_repositions_after_first_click_misses(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "id": 121,
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "rect", "title": "第1封", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "第2封", "x": 0.08, "y": 0.31875, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "邮件清单2", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.55},
        ],
    }
    target = {
        "runtime_index": 0,
        "id": "mail-0",
        "title": "香车馈赠",
        "create_time_text": "2026年08月18日07:42",
        "runtime_status": "unclaimed",
        "present_in_runtime": True,
        "locked": False,
        "action_policy": "claim",
    }

    class FakeRuntime:
        def __init__(self):
            self.ocr_calls = 0
            self.clear_calls = 0
            self.waits = []

        def cur_frame(self, *, update=False):
            assert update is True
            return f"frame-{self.ocr_calls}"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            self.ocr_calls += 1
            return []

        def wait_action_settle(self, seconds):
            self.waits.append(seconds)
            if False:
                yield None

        def clear_frame(self):
            self.clear_calls += 1

        def scroll_shape_content(self, *_args, **_kwargs):
            raise AssertionError("点击重试前不得滚动")

    monkeypatch.setattr(
        runner,
        "_ordered_runtime_window_mapping",
        lambda *_args, **_kwargs: {
            "runtime_offset": 0,
            "mappings": [
                {
                    "slot_index": 0,
                    "runtime_index": 0,
                    "mail_id": "mail-0",
                    "title": "香车馈赠",
                    "create_time_text": "2026年08月18日07:42",
                }
            ],
        },
    )
    outcomes = iter(
        [
            SimpleNamespace(wait_result="detail_not_found", visual_confirmed=False),
            SimpleNamespace(wait_result="list", visual_confirmed=True),
        ]
    )

    def fake_claim(*_args, **_kwargs):
        if False:
            yield None
        return next(outcomes)

    def fake_delete(*_args, **_kwargs):
        if False:
            yield None
        return 34

    monkeypatch.setattr(runner, "_claim_runtime_mail_row", fake_claim)
    monkeypatch.setattr(
        runner,
        "_delete_read_mail_until_clean",
        lambda *_args, **_kwargs: _return_generator(
            {
                "before_count": 0,
                "after_count": 0,
                "deleted_count": 0,
                "batch_count": 0,
                "protected_count": 0,
                "snapshot": {"items": []},
            }
        ),
    )
    monkeypatch.setattr(runner, "_leave_mail_scene_to_world", lambda *_args, **_kwargs: _return_generator("success"))
    monkeypatch.setattr(
        runner,
        "_read_complete_precise_mail_snapshot",
        lambda *_args, **_kwargs: {"items": []},
    )
    from pyxllib.autogui import View

    view121 = View(image)
    list_shape = view121.get_shape("邮件清单2")
    runtime = FakeRuntime()
    execution = runner._execute_ordered_runtime_claim_batch(
        {},
        threading.Event(),
        runtime=runtime,
        image121=image,
        view121=view121,
        list_shape=list_shape,
        geometry=mail_window_geometry_from_asset(image),
        snapshot={"items": [target]},
        target_mail_ids={"mail-0"},
    )
    with pytest.raises(StopIteration):
        while True:
            next(execution)

    assert runtime.ocr_calls == 2
    assert runtime.clear_calls == 1
    assert runtime.waits == [1.5]


def test_ordered_batch_waits_and_clears_transition_frame_after_scroll(monkeypatch) -> None:
    runner = create_behavior_tree_runtime_runner()
    image = {
        "id": 121,
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "rect", "title": "第1封", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "第2封", "x": 0.08, "y": 0.31875, "w": 0.84, "h": 0.1},
            {"kind": "rect", "title": "邮件清单2", "x": 0.08, "y": 0.2, "w": 0.84, "h": 0.55},
        ],
    }
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "香车馈赠",
            "create_time_text": f"2026年08月18日07:{42 - index:02d}",
            "runtime_status": "unclaimed" if index == 4 else "claimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim" if index == 4 else "",
        }
        for index in range(5)
    ]

    class FakeRuntime:
        def __init__(self):
            self.events = []

        def cur_frame(self, *, update=False):
            self.events.append("ocr")
            return "frame"

        def ocr_fragments_in_shapes(self, *_args, **_kwargs):
            return []

        def scroll_shape_content(self, *_args, **_kwargs):
            self.events.append("scroll")
            if False:
                yield None
            return True

        def wait_action_settle(self, seconds):
            self.events.append(("wait", seconds))
            if False:
                yield None

        def clear_frame(self):
            self.events.append("clear")

    windows = iter(
        [
            {
                "runtime_offset": 0,
                "mappings": [
                    {
                        "slot_index": index,
                        "runtime_index": index,
                        "mail_id": f"mail-{index}",
                        "title": "香车馈赠",
                        "create_time_text": f"2026年08月18日07:{42 - index:02d}",
                    }
                    for index in range(4)
                ],
            },
            RuntimeError("滚动稳定窗口首帧 OCR 不完整"),
            {
                "runtime_offset": 1,
                "mappings": [
                    {
                        "slot_index": 3,
                        "runtime_index": 4,
                        "mail_id": "mail-4",
                        "title": "香车馈赠",
                        "create_time_text": "2026年08月18日07:38",
                    }
                ],
            },
        ]
    )

    def next_window(*_args, **_kwargs):
        result = next(windows)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(runner, "_ordered_runtime_window_mapping", next_window)

    def fake_claim(*_args, **_kwargs):
        if False:
            yield None
        return SimpleNamespace(wait_result="list", visual_confirmed=True)

    def fake_delete(*_args, **_kwargs):
        if False:
            yield None
        return 34

    monkeypatch.setattr(runner, "_claim_runtime_mail_row", fake_claim)
    monkeypatch.setattr(
        runner,
        "_delete_read_mail_until_clean",
        lambda *_args, **_kwargs: _return_generator(
            {
                "before_count": 0,
                "after_count": 0,
                "deleted_count": 0,
                "batch_count": 0,
                "protected_count": 0,
                "snapshot": {"items": []},
            }
        ),
    )
    monkeypatch.setattr(runner, "_leave_mail_scene_to_world", lambda *_args, **_kwargs: _return_generator("success"))
    monkeypatch.setattr(runner, "_read_complete_precise_mail_snapshot", lambda *_args, **_kwargs: {"items": []})
    from pyxllib.autogui import View

    view121 = View(image)
    runtime = FakeRuntime()
    execution = runner._execute_ordered_runtime_claim_batch(
        {},
        threading.Event(),
        runtime=runtime,
        image121=image,
        view121=view121,
        list_shape=view121.get_shape("邮件清单2"),
        geometry=mail_window_geometry_from_asset(image),
        snapshot={"items": items},
    )
    with pytest.raises(StopIteration):
        while True:
            next(execution)

    assert runtime.events == [
        "ocr",
        "scroll",
        ("wait", 3.0),
        "clear",
        "ocr",
        ("wait", 1.0),
        "clear",
        "ocr",
    ]


def test_scrolled_stable_frame_refines_click_to_shifted_ocr_title_center() -> None:
    runner = create_behavior_tree_runtime_runner()
    geometry = SimpleNamespace(
        row_pitch=190.0,
        title_center_offset=-35.0,
    )
    fragments = [
        {"text": "香车馈赠", "x": 250, "y": 625, "w": 180, "h": 30},
        {"text": "香车馈赠", "x": 250, "y": 815, "w": 180, "h": 30},
    ]

    # The nominal slot title centre is 944, but the settled drag stopped the
    # intended row at y=830.  The default tight lattice guard rejects it;
    # the post-scroll refinement accepts that stable observed position.
    assert runner._precise_mail_observed_title_point(
        fragments,
        title="香车馈赠",
        fallback_y=979,
        geometry=geometry,
    ) is None
    assert runner._precise_mail_observed_title_point(
        fragments,
        title="香车馈赠",
        fallback_y=979,
        geometry=geometry,
        max_row_distance_ratio=0.8,
    ) == pytest.approx((340, 830))


def test_window_with_missing_visible_claim_mapping_fails_instead_of_scrolling() -> None:
    runner = create_behavior_tree_runtime_runner()

    with pytest.raises(RuntimeError, match="禁止滚动绕过"):
        runner._plan_precise_mail_window_action(
            [{"slot_index": 0, "runtime_index": 8, "mail_id": "other"}],
            [{"runtime_index": 8, "id": "claim-now", "action_policy": "claim"}],
        )


def test_ordered_runtime_window_never_exposes_footer_covered_fifth_slot() -> None:
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "rect", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
            {"kind": "rect", "title": "第2封", "x": 0, "y": 0.31875, "w": 1, "h": 0.1},
            {"kind": "rect", "title": "邮件清单2", "x": 0, "y": 0.2, "w": 1, "h": 0.6},
        ],
    }
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": title,
            "create_time_text": time_text,
        }
        for index, (title, time_text) in enumerate(
            [
                ("奇袭魔界奖励", "2026年08月18日13:00"),
                ("仙财福礼", "2026年08月18日12:00"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("香车馈赠", "2026年08月18日07:42"),
            ]
        )
    ]
    fragments = [
        {"text": "仙财福礼", "x": 200, "y": 565, "w": 160, "h": 20},
        {"text": "2026年08月18日12:00", "x": 200, "y": 595, "w": 260, "h": 20},
        {"text": "香车馈赠", "x": 200, "y": 755, "w": 160, "h": 20},
        {"text": "2026年08月18日07:42", "x": 200, "y": 785, "w": 260, "h": 20},
        {"text": "香车馈赠", "x": 200, "y": 945, "w": 160, "h": 20},
        {"text": "2026年08月18日07:42", "x": 200, "y": 975, "w": 260, "h": 20},
    ]

    window = create_behavior_tree_runtime_runner()._ordered_runtime_window_mapping(
        {"complete": True, "decoded_count": len(items), "items": items},
        image,
        fragments,
        previous_offset=-1,
        known_top=True,
    )

    assert [item["slot_index"] for item in window["mappings"]] == [0, 1, 2, 3]


def test_ordered_window_uses_exact_time_for_runtime_unknown_titles() -> None:
    image = {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "rect", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
            {"kind": "rect", "title": "第2封", "x": 0, "y": 0.31875, "w": 1, "h": 0.1},
            {"kind": "rect", "title": "邮件清单2", "x": 0, "y": 0.2, "w": 1, "h": 0.6},
        ],
    }
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": title,
            "create_time_text": time_text,
        }
        for index, (title, time_text) in enumerate(
            [
                ("奇袭魔界奖励", "2026年08月18日13:00"),
                ("仙财福礼", "2026年08月18日12:00"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("香车馈赠", "2026年08月18日07:42"),
                ("未知邮件类型80112", "2026年08月18日05:51"),
                ("未知邮件类型80112", "2026年08月18日05:46"),
                ("香车馈赠", "2026年08月18日05:24"),
            ]
        )
    ]
    fragments = [
        {"text": "香车馈赠", "x": 250, "y": 554, "w": 180, "h": 20},
        {"text": "2026年08月18日07:42", "x": 250, "y": 589, "w": 280, "h": 20},
        {"text": "玄荒古域奖励", "x": 250, "y": 744, "w": 220, "h": 20},
        {"text": "2026年08月18日05:51", "x": 250, "y": 779, "w": 280, "h": 20},
        {"text": "玄荒古域奖励", "x": 250, "y": 934, "w": 220, "h": 20},
        {"text": "2026年08月18日05:46", "x": 250, "y": 969, "w": 280, "h": 20},
    ]

    window = create_behavior_tree_runtime_runner()._ordered_runtime_window_mapping(
        {"items": items},
        image,
        fragments,
        previous_offset=2,
        known_top=False,
    )

    assert window["runtime_offset"] == 3
    assert window["anchor_count"] == 3
    assert window["mappings"][2]["title"] == "未知邮件类型80112"
    assert window["mappings"][2]["observed_title"] == "玄荒古域奖励"
    assert window["mappings"][3]["title"] == "未知邮件类型80112"
    assert window["mappings"][3]["observed_title"] == "玄荒古域奖励"

    restored_window = create_behavior_tree_runtime_runner()._ordered_runtime_window_mapping(
        {"items": items},
        image,
        fragments,
        previous_offset=3,
        known_top=False,
    )
    assert restored_window["runtime_offset"] == 3
    assert restored_window["anchor_count"] == 3

    rebounded_window = create_behavior_tree_runtime_runner()._ordered_runtime_window_mapping(
        {"items": items},
        image,
        fragments,
        previous_offset=4,
        known_top=False,
    )
    assert rebounded_window["runtime_offset"] == 3
    assert rebounded_window["anchor_count"] == 3


def test_precise_claim_allows_only_business_equivalent_ambiguous_offsets() -> None:
    runner = create_behavior_tree_runtime_runner()
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "香车馈赠",
            "create_time_text": "2026年08月18日05:21",
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim",
        }
        for index in range(5)
    ]
    diagnosis = {
        "status": "ambiguous",
        "visible_slots": [0, 1, 2],
        "observations": [
            {"slot_index": 0, "time_candidates": ["2026年08月18日05:21"]},
            {"slot_index": 1, "time_candidates": ["2026年08月18日05:21"]},
        ],
        "alignment": {
            "competitive_offsets": [1, 2],
            "hypotheses": [
                {"runtime_offset": 1, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0, 1]},
                {"runtime_offset": 2, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0, 1]},
            ],
        },
    }

    mapping = runner._safe_ambiguous_claim_mapping(
        {"items": items},
        items[1:4],
        diagnosis,
    )

    assert mapping is not None
    assert mapping["slot_index"] == 0
    assert mapping["equivalent_candidate_ids"] == ["mail-1", "mail-2"]


def test_precise_claim_rejects_repeated_title_equivalence_without_time() -> None:
    runner = create_behavior_tree_runtime_runner()
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "香车馈赠",
            "create_time_text": "2026年08月18日05:21",
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim",
        }
        for index in range(4)
    ]
    diagnosis = {
        "status": "ambiguous",
        "visible_slots": [0],
        "observations": [{"slot_index": 0, "time_candidates": []}],
        "alignment": {
            "competitive_offsets": [1, 2],
            "hypotheses": [
                {"runtime_offset": 1, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0]},
                {"runtime_offset": 2, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0]},
            ],
        },
    }

    assert runner._safe_ambiguous_claim_mapping(
        {"items": items},
        items[1:3],
        diagnosis,
    ) is None


def test_precise_claim_rejects_ambiguous_offsets_with_mixed_policy() -> None:
    runner = create_behavior_tree_runtime_runner()
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "香车馈赠",
            "create_time_text": "2026年08月18日05:21",
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": False,
            "action_policy": "claim" if index != 2 else "",
        }
        for index in range(4)
    ]
    diagnosis = {
        "status": "ambiguous",
        "visible_slots": [0],
        "observations": [
            {"slot_index": 0, "time_candidates": ["2026年08月18日05:21"]},
        ],
        "alignment": {
            "competitive_offsets": [1, 2],
            "hypotheses": [
                {"runtime_offset": 1, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0]},
                {"runtime_offset": 2, "anchor_count": 2, "exact_anchor_count": 0, "matched_slots": [0]},
            ],
        },
    }

    assert runner._safe_ambiguous_claim_mapping(
        {"items": items},
        [items[1]],
        diagnosis,
    ) is None


def test_precise_claim_ignores_near_score_hypothesis_with_weaker_exact_evidence() -> None:
    runner = create_behavior_tree_runtime_runner()
    items = [
        {
            "runtime_index": index,
            "id": f"mail-{index}",
            "title": "香车馈赠",
            "create_time_text": "2026年08月18日05:21",
            "runtime_status": "unclaimed",
            "present_in_runtime": True,
            "locked": index == 1,
            "action_policy": "" if index == 1 else "claim",
        }
        for index in range(5)
    ]
    diagnosis = {
        "status": "ambiguous",
        "visible_slots": [0],
        "observations": [
            {"slot_index": 0, "time_candidates": ["2026年08月18日05:21"]},
        ],
        "alignment": {
            "competitive_offsets": [1, 2, 3],
            "hypotheses": [
                {"runtime_offset": 1, "anchor_count": 3, "exact_anchor_count": 0, "matched_slots": [0]},
                {"runtime_offset": 2, "anchor_count": 3, "exact_anchor_count": 3, "matched_slots": [0]},
                {"runtime_offset": 3, "anchor_count": 3, "exact_anchor_count": 3, "matched_slots": [0]},
            ],
        },
    }

    mapping = runner._safe_ambiguous_claim_mapping(
        {"items": items},
        items[2:4],
        diagnosis,
    )

    assert mapping is not None
    assert mapping["equivalent_candidate_ids"] == ["mail-2", "mail-3"]
