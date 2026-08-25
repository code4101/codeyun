from __future__ import annotations

import pytest

from backend.core.fanxiu.data_annotation.dongtian_seat_geometry import (
    ATTENDANT_VISUAL_ORDER,
    DIRECT_MASTER_ENTRY_GROUPS,
    EMPTY_HITBOX_VERIFIED,
    MASTER_CONFIG_POINTS,
    resolve_dongtian_attendant_seat,
    resolve_dongtian_fixed_seat,
    resolve_dongtian_seat_gui_route,
    resolve_dongtian_target_gui_route,
)


EXPECTED_DEFAULT_POINTS = {
    4: (447, 886),
    5: (447, 754),
    6: (447, 1018),
    7: (585, 766),
    8: (723, 820),
    9: (647, 916),
    10: (247, 916),
    11: (171, 820),
    12: (309, 766),
}

EXPECTED_MASTER_DEFAULT_POINTS = {
    1: (436, 447),
    2: (165, 1192),
    3: (722, 1192),
}


def test_dongtian_attendant_default_geometry_covers_all_nine_seats() -> None:
    results = {
        seat_id: resolve_dongtian_attendant_seat(seat_id, group=4)
        for seat_id in EXPECTED_DEFAULT_POINTS
    }

    assert {seat_id: result.point for seat_id, result in results.items()} == EXPECTED_DEFAULT_POINTS
    assert tuple(
        seat_id
        for seat_id, _result in sorted(results.items(), key=lambda item: item[1].visual_rank)
    ) == ATTENDANT_VISUAL_ORDER
    assert all(result.visual_order == ATTENDANT_VISUAL_ORDER for result in results.values())
    assert all(result.calibration_source == "reference_900x1600" for result in results.values())
    assert EMPTY_HITBOX_VERIFIED is False
    assert all(result.empty_hitbox_verified is False for result in results.values())


def test_fudi_group4_fixed_geometry_covers_all_twelve_runtime_seats() -> None:
    results = {
        seat_id: resolve_dongtian_fixed_seat(
            1 if seat_id in MASTER_CONFIG_POINTS else 2,
            seat_id,
            group=4,
        )
        for seat_id in (*EXPECTED_MASTER_DEFAULT_POINTS, *EXPECTED_DEFAULT_POINTS)
    }

    assert DIRECT_MASTER_ENTRY_GROUPS == frozenset({4})
    assert {
        seat_id: result.point
        for seat_id, result in results.items()
    } == {**EXPECTED_MASTER_DEFAULT_POINTS, **EXPECTED_DEFAULT_POINTS}
    assert all(result.viewport == (900, 1600) for result in results.values())
    assert all(
        result.calibration_source == "reference_900x1600"
        for result in results.values()
    )


def test_fudi_fixed_geometry_requires_explicit_calibration_outside_reference_viewport() -> None:
    with pytest.raises(ValueError, match="缺少显式 origin/scale"):
        resolve_dongtian_fixed_seat(
            1,
            1,
            group=4,
            viewport=(1800, 3200),
        )


@pytest.mark.parametrize("group", [1, 2, 3, 4])
def test_dongtian_attendant_geometry_accepts_only_ordinary_groups(group: int) -> None:
    assert resolve_dongtian_attendant_seat(5, group=group).point == (447, 754)


@pytest.mark.parametrize("group", [0, 5, 99])
def test_dongtian_attendant_geometry_rejects_unsupported_groups(group: int) -> None:
    with pytest.raises(ValueError, match=r"group 1\.\.4"):
        resolve_dongtian_attendant_seat(5, group=group)


@pytest.mark.parametrize("seat_id", [1, 3, 13, 99])
def test_dongtian_attendant_geometry_rejects_unknown_or_lord_seats(seat_id: int) -> None:
    with pytest.raises(ValueError, match="seat_id"):
        resolve_dongtian_attendant_seat(seat_id, group=1)


def test_dongtian_attendant_geometry_requires_calibration_for_other_viewports() -> None:
    with pytest.raises(ValueError, match="缺少显式 origin/scale"):
        resolve_dongtian_attendant_seat(7, group=1, viewport=(1800, 3200))


@pytest.mark.parametrize(
    ("origin", "scale"),
    [
        ((894, 1640), None),
        (None, 2.4),
    ],
)
def test_dongtian_attendant_geometry_rejects_partial_calibration(origin, scale) -> None:
    with pytest.raises(ValueError, match="同时提供"):
        resolve_dongtian_attendant_seat(
            7,
            group=1,
            viewport=(1800, 3200),
            origin=origin,
            scale=scale,
        )


def test_dongtian_attendant_geometry_uses_explicit_calibration() -> None:
    result = resolve_dongtian_attendant_seat(
        10,
        group=2,
        viewport=(1800, 3200),
        origin=(894, 1640),
        scale=(2.4, 2.4),
    )

    assert result.point == (493, 1832)
    assert result.calibration_source == "explicit"
    assert result.empty_hitbox_verified is False


def test_dongtian_attendant_geometry_rejects_projection_outside_viewport() -> None:
    with pytest.raises(ValueError, match="超出 viewport"):
        resolve_dongtian_attendant_seat(
            8,
            group=3,
            viewport=(100, 100),
            origin=(50, 50),
            scale=10,
        )


def _shape(x: float, y: float, w: float, h: float, landing: str) -> dict:
    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "sceneJumpTarget": landing,
    }


@pytest.mark.parametrize("seat_id", [1, 2, 3])
def test_existing_group3_master_seats_keep_asset_backed_native_route(seat_id: int) -> None:
    route = resolve_dongtian_seat_gui_route(
        quality=1,
        seat_id=seat_id,
        group=3,
        occupancy="empty",
        scroll_offset_verified=True,
        scene_341_shapes={
            "位置1": _shape(0.4055555556, 0.2989583333, 0.1666666667, 0.0697916667, "342(63)"),
        },
        scene_342_shapes={
            "占领": _shape(0.4351851852, 0.703125, 0.1425925926, 0.040625, "343(60)"),
        },
    )

    assert route.available is True
    assert route.verified_for_click is True
    assert route.ui_route == "master_list_first_empty"
    assert [(step.scene_id, step.shape_title) for step in route.steps] == [
        (341, "位置1"),
        (342, "占领"),
    ]
    assert [step.point for step in route.steps] == [(440, 534), (456, 1158)]


@pytest.mark.parametrize(
    ("seat_id", "expected_point"),
    sorted(EXPECTED_MASTER_DEFAULT_POINTS.items()),
)
def test_fudi_group4_master_uses_runtime_seat_fixed_entry(
    seat_id: int,
    expected_point: tuple[int, int],
) -> None:
    route = resolve_dongtian_seat_gui_route(
        quality=1,
        seat_id=seat_id,
        group=4,
        occupancy="empty",
        scroll_offset_verified=True,
        scene_341_shapes={
            # A stale unrelated shape must not affect the 福地 entry point.
            "位置1": _shape(0.1, 0.1, 0.1, 0.1, "342"),
        },
        scene_342_shapes={
            "占领": _shape(0.4351851852, 0.703125, 0.1425925926, 0.040625, "343(60)"),
        },
    )

    assert route.available is True
    assert route.verified_for_click is False
    assert route.ui_route == "master_list_first_empty"
    assert route.steps[0].locator_kind == "projected_point"
    assert route.steps[0].shape_title is None
    assert route.steps[0].point == expected_point
    assert route.steps[1].point == (456, 1158)
    assert route.blockers == ("empty_master_hitbox_unverified",)


def test_fudi_master_direct_entry_needs_separate_hitbox_evidence() -> None:
    common = {
        "quality": 1,
        "seat_id": 1,
        "group": 4,
        "occupancy": "empty",
        "scroll_offset_verified": True,
        "scene_342_shapes": {
            "占领": _shape(0.4, 0.7, 0.1, 0.1, "343"),
        },
    }
    occupied_only = resolve_dongtian_seat_gui_route(
        **common,
        occupied_hitbox_verified=True,
    )
    empty_verified = resolve_dongtian_seat_gui_route(
        **common,
        empty_hitbox_verified=True,
    )

    assert occupied_only.verified_for_click is False
    assert occupied_only.blockers == ("empty_master_hitbox_unverified",)
    assert empty_verified.verified_for_click is True
    assert empty_verified.blockers == ()


def test_master_shared_route_rejects_occupied_target_and_unverified_landing() -> None:
    route = resolve_dongtian_seat_gui_route(
        quality=1,
        seat_id=2,
        group=3,
        occupancy="occupied",
        scroll_offset_verified=True,
        scene_341_shapes={"位置1": _shape(0.4, 0.3, 0.1, 0.1, "342")},
        scene_342_shapes={"占领": _shape(0.4, 0.7, 0.1, 0.1, "")},
    )

    assert route.available is True
    assert route.verified_for_click is False
    assert "scene_342_master_action_landing_unverified" in route.blockers
    assert "master_shared_route_requires_empty_target" in route.blockers


def test_master_nondefault_resolution_remains_preview_only() -> None:
    route = resolve_dongtian_seat_gui_route(
        quality=1,
        seat_id=1,
        group=1,
        occupancy="empty",
        viewport=(1800, 3200),
        scroll_offset_verified=True,
        scene_341_shapes={"位置1": _shape(0.4, 0.3, 0.1, 0.1, "342")},
        scene_342_shapes={"占领": _shape(0.4, 0.7, 0.1, 0.1, "343")},
    )

    assert route.available is True
    assert route.verified_for_click is False
    assert "nondefault_master_viewport_unverified" in route.blockers


def test_empty_follower_requires_both_scroll_and_empty_hitbox_evidence() -> None:
    route = resolve_dongtian_seat_gui_route(
        quality=2,
        seat_id=4,
        group=1,
        occupancy="empty",
    )

    assert route.available is True
    assert route.steps[0].point == (447, 886)
    assert route.verified_for_click is False
    assert route.blockers == (
        "scroll_offset_unverified",
        "empty_follower_hitbox_unverified",
    )

    verified = resolve_dongtian_seat_gui_route(
        quality=2,
        seat_id=4,
        group=1,
        occupancy="empty",
        scroll_offset_verified=True,
        empty_hitbox_verified=True,
    )
    assert verified.verified_for_click is True
    assert verified.blockers == ()


def test_occupied_follower_evidence_does_not_authorize_empty_hitbox() -> None:
    occupied = resolve_dongtian_seat_gui_route(
        quality=2,
        seat_id=8,
        group=2,
        occupancy="occupied",
        scroll_offset_verified=True,
        occupied_hitbox_verified=True,
    )
    empty = resolve_dongtian_seat_gui_route(
        quality=2,
        seat_id=8,
        group=2,
        occupancy="empty",
        scroll_offset_verified=True,
        occupied_hitbox_verified=True,
    )

    assert occupied.verified_for_click is True
    assert empty.verified_for_click is False
    assert empty.blockers == ("empty_follower_hitbox_unverified",)


def test_follower_nondefault_resolution_still_requires_explicit_calibration() -> None:
    with pytest.raises(ValueError, match="缺少显式 origin/scale"):
        resolve_dongtian_seat_gui_route(
            quality=2,
            seat_id=7,
            group=1,
            occupancy="occupied",
            viewport=(1800, 3200),
            scroll_offset_verified=True,
            occupied_hitbox_verified=True,
        )


def test_runtime_target_identity_and_route_are_cross_checked() -> None:
    route = resolve_dongtian_target_gui_route(
        {
            "mine_id": 7,
            "quality": 2,
            "seat_id": 8,
            "seat_key": "7:2:4",
            "ui_route": "master_list_first_empty",
            "mode": "replace_weaker_enemy",
            "friendly_place": True,
        },
        group=2,
        scroll_offset_verified=True,
        occupied_hitbox_verified=True,
    )

    assert route.steps[0].point == (723, 820)
    assert route.verified_for_click is False
    assert "runtime_seat_key_missing_or_mismatched" in route.blockers
    assert "runtime_ui_route_mismatched" in route.blockers


def test_runtime_master_target_requires_friendly_first_empty_contract() -> None:
    route = resolve_dongtian_target_gui_route(
        {
            "mine_id": 7,
            "quality": 1,
            "seat_id": 2,
            "seat_key": "7:1:2",
            "ui_route": "master_list_first_empty",
            "mode": "occupy_empty",
            "friendly_place": False,
        },
        group=3,
        scroll_offset_verified=True,
        scene_341_shapes={"位置1": _shape(0.4, 0.3, 0.1, 0.1, "342")},
        scene_342_shapes={"占领": _shape(0.4, 0.7, 0.1, 0.1, "343")},
    )

    assert route.verified_for_click is False
    assert route.blockers == ("master_shared_route_requires_friendly_mine",)
