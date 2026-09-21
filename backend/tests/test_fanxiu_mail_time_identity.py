"""Focused pure-algorithm regression tests for mail time identity.

These tests only exercise in-memory OCR/Runtime value objects.  They never touch
a live Runtime, GUI, Kernel or external data directory.
"""

from __future__ import annotations

import pytest

# Import the executor shim before ``tasks.mail`` so its module-level import of
# ``MailTaskMixin`` resolves against a fully initialized package (same ordering
# the existing selective-claim tests rely on).
import backend.core.fanxiu.data_annotation.behavior_tree_executor  # noqa: F401
from backend.core.fanxiu.runtime_gui.mail import (
    MailVisualObservation,
    align_mail_window,
    best_mail_time_relation,
    mail_runtime_time_key,
    mail_time_is_match,
    mail_time_relation,
)
from backend.core.fanxiu.data_annotation.tasks.mail import (
    MailTaskMixin,
    _MailWindowAmbiguous,
)

_DATE_OLD = "2026年08月30日23:59"
_DATE_NEW = "2026年09月13日23:59"


def _image121() -> dict:
    return {
        "width": 900,
        "height": 1600,
        "shapes": [
            {"kind": "shape", "title": "第1封", "x": 0, "y": 0.2, "w": 1, "h": 0.1},
            {"kind": "shape", "title": "第2封", "x": 0, "y": 0.31875, "w": 1, "h": 0.1},
            {"kind": "shape", "title": "邮件清单2", "x": 0, "y": 0.2, "w": 1, "h": 0.6},
        ],
    }


def _fragments(rows: list[tuple[str, str]]) -> list[dict]:
    """Build title/time OCR fragments for slots 0..3 on the 190px lattice."""

    centers = (400.0, 590.0, 780.0, 970.0)
    fragments: list[dict] = []
    for index, (title, time_text) in enumerate(rows):
        y = centers[index]
        if title:
            fragments.append({"text": title, "y": y})
        if time_text:
            fragments.append({"text": time_text, "y": y})
    return fragments


def _snapshot(items: list[dict]) -> dict:
    return {"complete": True, "decoded_count": len(items), "items": items}


def _item(index: int, title: str, time_text: str) -> dict:
    return {
        "runtime_index": index,
        "id": f"m{index}",
        "title": title,
        "create_time_text": time_text,
    }


# --------------------------------------------------------------------------- #
# Shared time helper
# --------------------------------------------------------------------------- #


def test_complete_date_conflict_is_never_downgraded_to_hhmm() -> None:
    assert mail_time_relation(_DATE_NEW, "202609132359") == "exact"
    assert mail_time_relation(_DATE_NEW, "202608302359") == "conflict"
    assert mail_time_is_match("conflict") is False


def test_only_hhmm_yields_a_restricted_weak_match() -> None:
    assert mail_time_relation("23:59", "202609132359") == "weak"
    assert mail_time_relation(_DATE_NEW, "2359") == "weak"
    assert mail_time_is_match("weak") is True


def test_full_date_conflict_outranks_a_date_less_weak_reading() -> None:
    relation = best_mail_time_relation(["23:59", _DATE_OLD], "202609132359")
    assert relation == "conflict"
    assert mail_time_is_match(relation) is False


def test_epoch_is_normalized_before_comparison() -> None:
    key = mail_runtime_time_key({"create_time_ms": 1_700_000_000_000})
    assert len(key) == 12 and key.isdigit()
    candidate = f"{key[0:4]}年{key[4:6]}月{key[6:8]}日{key[8:10]}:{key[10:12]}"
    assert mail_time_relation(candidate, key) == "exact"

    conflicting = key[:6] + ("01" if key[6:8] != "01" else "02") + key[8:]
    assert conflicting[-4:] == key[-4:]
    assert mail_time_relation(candidate, conflicting) == "conflict"


# --------------------------------------------------------------------------- #
# Shared align algorithm
# --------------------------------------------------------------------------- #


def test_align_selects_the_true_date_over_same_hhmm_title_twins() -> None:
    rows = [
        _item(index, "重名", _DATE_OLD if index < 4 else _DATE_NEW)
        for index in range(8)
    ]
    observations = [
        MailVisualObservation(slot, 0.0, ("重名",), (_DATE_NEW,))
        for slot in range(4)
    ]

    result = align_mail_window(rows, observations, visible_slots=range(4))

    assert result.status == "aligned"
    assert result.runtime_offset == 4
    assert [item["mail_id"] for item in result.mappings] == ["m4", "m5", "m6", "m7"]


def test_align_does_not_anchor_a_conflicting_date_on_a_strong_title() -> None:
    rows = [
        _item(0, "独有邮件", _DATE_OLD),
        _item(1, "占位邮件", "2026年09月20日10:00"),
    ]
    observations = [MailVisualObservation(0, 0.0, ("独有邮件",), (_DATE_NEW,))]

    result = align_mail_window(rows, observations, visible_slots=range(1))

    assert result.status != "aligned"
    assert result.runtime_offset is None
    assert all(item.anchor_count == 0 for item in result.hypotheses)


def test_align_keeps_true_duplicate_dates_ambiguous() -> None:
    rows = [_item(index, "重名", _DATE_NEW) for index in range(8)]
    observations = [
        MailVisualObservation(slot, 0.0, ("重名",), (_DATE_NEW,))
        for slot in range(4)
    ]

    result = align_mail_window(rows, observations, visible_slots=range(4))

    assert result.status == "ambiguous"
    assert result.runtime_offset is None


def test_align_hhmm_only_cannot_fabricate_a_unique_date_identity() -> None:
    rows = [
        _item(index, "重名", _DATE_OLD if index < 4 else _DATE_NEW)
        for index in range(8)
    ]
    observations = [
        MailVisualObservation(slot, 0.0, ("重名",), ("23:59",))
        for slot in range(4)
    ]

    result = align_mail_window(rows, observations, visible_slots=range(4))

    assert result.status == "ambiguous"
    assert result.runtime_offset is None


# --------------------------------------------------------------------------- #
# Ordered runtime window mapping (the observed failure branch)
# --------------------------------------------------------------------------- #


def test_ordered_mapping_picks_the_matching_full_date_over_hhmm_twins() -> None:
    rows = [
        _item(index, "重名", _DATE_OLD if index < 4 else _DATE_NEW)
        for index in range(8)
    ]
    fragments = _fragments([("重名", _DATE_NEW)] * 4)

    window = MailTaskMixin._ordered_runtime_window_mapping(
        _snapshot(rows),
        _image121(),
        fragments,
        previous_offset=0,
        known_top=False,
    )

    assert window["runtime_offset"] == 4
    assert [item["mail_id"] for item in window["mappings"]] == [
        "m4",
        "m5",
        "m6",
        "m7",
    ]


def test_ordered_mapping_hhmm_only_does_not_resolve_cross_date_twins() -> None:
    rows = [
        _item(index, "重名", _DATE_OLD if index < 4 else _DATE_NEW)
        for index in range(8)
    ]
    fragments = _fragments([("重名", "23:59")] * 4)

    with pytest.raises(_MailWindowAmbiguous):
        MailTaskMixin._ordered_runtime_window_mapping(
            _snapshot(rows),
            _image121(),
            fragments,
            previous_offset=0,
            known_top=False,
        )


def test_ordered_mapping_true_duplicate_dates_stay_ambiguous() -> None:
    rows = [_item(index, "重名", _DATE_NEW) for index in range(8)]
    fragments = _fragments([("重名", _DATE_NEW)] * 4)

    with pytest.raises(_MailWindowAmbiguous):
        MailTaskMixin._ordered_runtime_window_mapping(
            _snapshot(rows),
            _image121(),
            fragments,
            previous_offset=0,
            known_top=False,
        )


def test_ordered_mapping_top_branch_vetoes_conflicting_dates() -> None:
    rows = [
        _item(0, "独有邮件", _DATE_NEW),
        _item(1, "重名一", _DATE_OLD),
        _item(2, "重名二", _DATE_OLD),
        _item(3, "重名三", _DATE_OLD),
    ]
    fragments = _fragments(
        [
            ("", ""),
            ("重名一", _DATE_NEW),
            ("重名二", _DATE_NEW),
            ("重名三", _DATE_NEW),
        ]
    )

    with pytest.raises(RuntimeError):
        MailTaskMixin._ordered_runtime_window_mapping(
            _snapshot(rows),
            _image121(),
            fragments,
            previous_offset=-1,
            known_top=True,
        )
