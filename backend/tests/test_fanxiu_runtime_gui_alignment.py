from __future__ import annotations

from backend.core.fanxiu.runtime_gui import (
    GuiCandidate,
    RuntimeEntity,
    align_runtime_gui_candidates,
    validate_runtime_evidence,
)


def test_imperfect_ocr_can_locate_authoritative_runtime_entity() -> None:
    result = align_runtime_gui_candidates(
        [RuntimeEntity(key="magic-invasion", name="魔道入侵")],
        [GuiCandidate(key="card-2", text="魔道人侵（预赛）", point=(420, 760))],
        runtime_complete=True,
    )

    assert result.aligned
    assert result.mappings[0].entity.name == "魔道入侵"
    assert result.mappings[0].candidate.point == (420, 760)
    assert result.mappings[0].evidence.text_score < 1.0
    assert result.mappings[0].evidence.text_score >= 0.55


def test_runtime_identity_stays_authoritative_when_alias_matches_gui() -> None:
    result = align_runtime_gui_candidates(
        [RuntimeEntity(key="beast-abyss", name="兽渊探秘", aliases=("兽渊",))],
        [GuiCandidate(key="current-card", text="兽渊")],
        runtime_complete=True,
    )

    assert result.aligned
    assert result.mappings[0].entity.name == "兽渊探秘"
    assert result.mappings[0].evidence.matched_alias == "兽渊"


def test_context_can_align_coordinate_when_ocr_is_missing() -> None:
    result = align_runtime_gui_candidates(
        [RuntimeEntity(key="mail-17", name="宗门奖励", payload={"runtime_index": 17})],
        [GuiCandidate(key="slot-3", text="", slot=3, point=(330, 512))],
        runtime_complete=True,
        context_scorer=lambda entity, candidate: (
            1.0
            if entity.payload.get("runtime_index") == 17 and candidate.slot == 3
            else 0.0
        ),
        minimum_pair_score=0.9,
    )

    assert result.aligned
    assert result.mappings[0].candidate.key == "slot-3"
    assert result.mappings[0].evidence.text_score == 0.0
    assert result.mappings[0].evidence.context_score == 1.0


def test_incomplete_runtime_snapshot_fails_closed() -> None:
    result = align_runtime_gui_candidates(
        [{"id": "mail-1", "name": "系统补偿"}],
        [{"id": "row-1", "text": "系统补偿", "point": (10, 20)}],
        runtime_complete=False,
    )

    assert not result.aligned
    assert result.status == "incomplete_runtime"
    assert result.mappings == ()


def test_equal_gui_candidates_are_ambiguous_instead_of_clicking_first() -> None:
    result = align_runtime_gui_candidates(
        [RuntimeEntity(key="activity", name="兽渊探秘")],
        [
            GuiCandidate(key="left", text="兽渊探秘", point=(100, 500)),
            GuiCandidate(key="right", text="兽渊探秘", point=(500, 500)),
        ],
        runtime_complete=True,
    )

    assert not result.aligned
    assert result.status == "ambiguous"
    assert result.mappings == ()


def test_global_assignment_uses_all_names_not_greedy_first_match() -> None:
    result = align_runtime_gui_candidates(
        [
            RuntimeEntity(key="magic", name="魔道入侵"),
            RuntimeEntity(key="beast", name="兽渊探秘"),
        ],
        [
            GuiCandidate(key="slot-1", text="兽渊探秘"),
            GuiCandidate(key="slot-2", text="魔道人侵"),
        ],
        runtime_complete=True,
    )

    assert result.aligned
    assert [(item.entity.key, item.candidate.key) for item in result.mappings] == [
        ("magic", "slot-2"),
        ("beast", "slot-1"),
    ]


def test_runtime_evidence_requires_fresh_process_identity() -> None:
    valid = validate_runtime_evidence(
        {
            "complete": True,
            "captured_at_epoch": 100.0,
            "sequence_fingerprint": "abc",
            "evidence": {"pid": 77, "process_start_ticks": 1234},
        },
        max_age_seconds=5.0,
        now_epoch=103.0,
    )
    stale = validate_runtime_evidence(
        {
            "complete": True,
            "captured_at_epoch": 90.0,
            "evidence": {"pid": 77, "process_start_ticks": 1234},
        },
        max_age_seconds=5.0,
        now_epoch=103.0,
    )
    unidentified = validate_runtime_evidence(
        {"complete": True, "captured_at_epoch": 103.0, "evidence": {}},
        max_age_seconds=5.0,
        now_epoch=103.0,
    )

    assert valid.ok and valid.fingerprint == "abc"
    assert not stale.ok and "过期" in stale.reason
    assert not unidentified.ok and "进程身份" in unidentified.reason
