from __future__ import annotations

"""Pure geometry for Runtime-identified seats on Dongtian scene #341.

Runtime owns seat identity and business eligibility.  This module only maps a
validated ``(group, quality, seat_id)`` (the native ``siteCfg`` identity) to a
candidate GUI point or an explicitly retained asset-backed route.  It
deliberately does not claim that a projected hitbox has been verified on a real
frame.
"""

from dataclasses import dataclass, replace
import math
from numbers import Real
from typing import Any, Literal, Mapping, Sequence


Viewport = tuple[int, int]
PixelPoint = tuple[int, int]
ConfigPoint = tuple[int, int]
CalibrationSource = Literal["reference_900x1600", "explicit"]
SeatOccupancy = Literal["empty", "occupied", "unknown"]

DEFAULT_VIEWPORT: Viewport = (900, 1600)
DEFAULT_ORIGIN = (447.0, 820.0)
DEFAULT_SCALE = (1.2, 1.2)
SUPPORTED_GROUPS = frozenset({1, 2, 3, 4})

# Native XianLvMines_Miner rows.  All ordinary groups 1..4 reuse these twelve
# ``minesCoord`` values.  XianLvMinesPointInfoPanel instantiates one
# XianLvMinesSiteItem per config row; that item anchors its root at minesCoord
# and binds all visible empty/occupied icons to the same OnClick handler.
MASTER_CONFIG_POINTS: dict[int, ConfigPoint] = {
    1: (-9, 311),
    2: (-235, -310),
    3: (229, -310),
}

ATTENDANT_CONFIG_POINTS: dict[int, ConfigPoint] = {
    4: (0, -55),
    5: (0, 55),
    6: (0, -165),
    7: (115, 45),
    8: (230, 0),
    9: (167, -80),
    10: (-167, -80),
    11: (-230, 0),
    12: (-115, 45),
}

SEAT_CONFIG_POINTS: dict[int, ConfigPoint] = {
    **MASTER_CONFIG_POINTS,
    **ATTENDANT_CONFIG_POINTS,
}

# Screen top-to-bottom, then left-to-right within one visual row.
ATTENDANT_VISUAL_ORDER = (5, 12, 7, 11, 8, 4, 10, 9, 6)
MASTER_VISUAL_ORDER = (1, 2, 3)

# Native Lua proves that noDefStateIcon is clickable, but its child hitbox has
# not yet been exercised on a real empty-seat frame.  Keep this fail-closed.
EMPTY_HITBOX_VERIFIED = False
OCCUPIED_HITBOX_VERIFIED = False

MASTER_SHARED_ENTRY_SHAPE = "位置1"
MASTER_SHARED_ACTION_SHAPE = "占领"

# The native layout proves direct coordinates for every ordinary group, but
# only group 4 (福地) currently needs the fixed-point correction.  Groups 1..3
# retain the already exercised asset-backed master entrance until equivalent
# live click evidence promotes them independently.
DIRECT_MASTER_ENTRY_GROUPS = frozenset({4})


@dataclass(frozen=True)
class DongtianSeatGuiStep:
    """One read-only GUI route step for a Runtime-authorized seat."""

    scene_id: int
    locator_kind: Literal["asset_shape", "projected_point"]
    shape_title: str | None
    point: PixelPoint | None
    expected_scene_ids: tuple[int, ...]
    verified_for_click: bool


@dataclass(frozen=True)
class DongtianSeatGuiRoute:
    """Identity-preserving route preview; it never performs a click."""

    quality: int
    seat_id: int
    group: int
    occupancy: SeatOccupancy
    ui_route: str
    viewport: Viewport
    steps: tuple[DongtianSeatGuiStep, ...]
    blockers: tuple[str, ...]

    @property
    def available(self) -> bool:
        return all(step.point is not None for step in self.steps)

    @property
    def verified_for_click(self) -> bool:
        return self.available and not self.blockers and all(
            step.verified_for_click for step in self.steps
        )


@dataclass(frozen=True)
class DongtianSeatGeometry:
    """One identity-preserving #341 seat location candidate."""

    quality: int
    seat_id: int
    group: int
    viewport: Viewport
    config_point: ConfigPoint
    point: PixelPoint
    visual_rank: int
    calibration_source: CalibrationSource

    @property
    def visual_order(self) -> tuple[int, ...]:
        return MASTER_VISUAL_ORDER if self.quality == 1 else ATTENDANT_VISUAL_ORDER

    @property
    def empty_hitbox_verified(self) -> bool:
        """Always false until a real empty-seat click is transaction-verified."""

        return EMPTY_HITBOX_VERIFIED


def _viewport(value: Sequence[int]) -> Viewport:
    try:
        width, height = value
    except (TypeError, ValueError) as exc:
        raise ValueError("洞天席位 viewport 必须是 (width, height)") from exc
    if isinstance(width, bool) or isinstance(height, bool):
        raise ValueError("洞天席位 viewport 必须使用正整数")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("洞天席位 viewport 必须使用正整数")
    return (width, height)


def _pair(value: Sequence[Real], *, label: str) -> tuple[float, float]:
    try:
        first, second = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"洞天席位 {label} 必须包含两个有限数值") from exc
    if isinstance(first, bool) or isinstance(second, bool):
        raise ValueError(f"洞天席位 {label} 必须包含两个有限数值")
    if not isinstance(first, Real) or not isinstance(second, Real):
        raise ValueError(f"洞天席位 {label} 必须包含两个有限数值")
    pair = (float(first), float(second))
    if not all(math.isfinite(item) for item in pair):
        raise ValueError(f"洞天席位 {label} 必须包含两个有限数值")
    return pair


def _scale_pair(value: Real | Sequence[Real]) -> tuple[float, float]:
    if isinstance(value, bool):
        raise ValueError("洞天席位 scale 必须是正数或两个正数")
    if isinstance(value, Real):
        pair = (float(value), float(value))
    else:
        pair = _pair(value, label="scale")
    if not all(item > 0 for item in pair):
        raise ValueError("洞天席位 scale 必须是正数或两个正数")
    return pair


def _normalized_shape_center(
    shape: Mapping[str, Any] | None,
    *,
    viewport: Viewport,
) -> PixelPoint | None:
    """Resolve an asset Shape center without copying its pixel geometry."""

    if not isinstance(shape, Mapping):
        return None
    values: list[float] = []
    for key in ("x", "y", "w", "h"):
        value = shape.get(key)
        if isinstance(value, bool) or not isinstance(value, Real):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        values.append(number)
    x, y, width, height = values
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        return None
    viewport_width, viewport_height = viewport
    point = (
        int(round((x + width / 2) * viewport_width)),
        int(round((y + height / 2) * viewport_height)),
    )
    if not 0 <= point[0] < viewport_width or not 0 <= point[1] < viewport_height:
        return None
    return point


def _shape_has_observed_landing(
    shape: Mapping[str, Any] | None,
    expected_scene_id: int,
) -> bool:
    if not isinstance(shape, Mapping):
        return False
    raw = str(shape.get("sceneJumpTarget") or "")
    for token in raw.split(","):
        scene_token = token.strip().split("(", 1)[0].strip()
        if scene_token.isdigit() and int(scene_token) == int(expected_scene_id):
            return True
    return False


def _normalize_occupancy(value: str) -> SeatOccupancy:
    normalized = str(value or "").strip().lower()
    if normalized not in {"empty", "occupied", "unknown"}:
        raise ValueError("洞天席位 occupancy 必须是 empty/occupied/unknown")
    return normalized  # type: ignore[return-value]


def resolve_dongtian_fixed_seat(
    quality: int,
    seat_id: int,
    *,
    group: int,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    origin: Sequence[Real] | None = None,
    scale: Real | Sequence[Real] | None = None,
) -> DongtianSeatGeometry:
    """Map one Runtime seat identity to a #341 candidate click point.

    The built-in calibration is valid only for the two independently observed
    900x1600 default-view frames.  Any other viewport must provide both a
    measured ``origin`` and positive ``scale``.  Unity config uses y-up, so the
    screen projection is ``(ox + sx*x, oy - sy*y)``.
    """

    if isinstance(group, bool) or not isinstance(group, int) or group not in SUPPORTED_GROUPS:
        raise ValueError("洞天席位只支持普通矿区 group 1..4")
    if isinstance(quality, bool) or not isinstance(quality, int) or quality not in {1, 2}:
        raise ValueError("洞天席位 quality 必须是 1(尊主) 或 2(侍从)")
    expected_ids = MASTER_CONFIG_POINTS if quality == 1 else ATTENDANT_CONFIG_POINTS
    if isinstance(seat_id, bool) or not isinstance(seat_id, int) or seat_id not in expected_ids:
        expected_range = "1..3" if quality == 1 else "4..12"
        raise ValueError(f"洞天席位 quality={quality} 的 seat_id 必须是 {expected_range}")

    normalized_viewport = _viewport(viewport)
    if (origin is None) != (scale is None):
        raise ValueError("洞天席位显式校准必须同时提供 origin 与 scale")
    if origin is None:
        if normalized_viewport != DEFAULT_VIEWPORT:
            raise ValueError("非 900x1600 洞天画面缺少显式 origin/scale 校准")
        origin_pair = DEFAULT_ORIGIN
        scale_pair = DEFAULT_SCALE
        calibration_source: CalibrationSource = "reference_900x1600"
    else:
        origin_pair = _pair(origin, label="origin")
        scale_pair = _scale_pair(scale)
        calibration_source = "explicit"

    config_x, config_y = expected_ids[seat_id]
    x = int(round(origin_pair[0] + scale_pair[0] * config_x))
    y = int(round(origin_pair[1] - scale_pair[1] * config_y))
    width, height = normalized_viewport
    if not 0 <= x < width or not 0 <= y < height:
        raise ValueError("洞天席位校准结果超出 viewport，拒绝生成点击候选")

    return DongtianSeatGeometry(
        quality=quality,
        seat_id=seat_id,
        group=group,
        viewport=normalized_viewport,
        config_point=expected_ids[seat_id],
        point=(x, y),
        visual_rank=(
            MASTER_VISUAL_ORDER.index(seat_id)
            if quality == 1
            else ATTENDANT_VISUAL_ORDER.index(seat_id)
        ),
        calibration_source=calibration_source,
    )


def resolve_dongtian_attendant_seat(
    seat_id: int,
    *,
    group: int,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    origin: Sequence[Real] | None = None,
    scale: Real | Sequence[Real] | None = None,
) -> DongtianSeatGeometry:
    """Backward-compatible follower-only projection helper."""

    return resolve_dongtian_fixed_seat(
        2,
        seat_id,
        group=group,
        viewport=viewport,
        origin=origin,
        scale=scale,
    )


def resolve_dongtian_seat_gui_route(
    *,
    quality: int,
    seat_id: int,
    group: int,
    occupancy: str,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    scene_341_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    scene_342_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    origin: Sequence[Real] | None = None,
    scale: Real | Sequence[Real] | None = None,
    scroll_offset_verified: bool = False,
    occupied_hitbox_verified: bool = OCCUPIED_HITBOX_VERIFIED,
    empty_hitbox_verified: bool = EMPTY_HITBOX_VERIFIED,
) -> DongtianSeatGuiRoute:
    """Map one Runtime seat to an observation-only #341 GUI route.

    Runtime has already decided *which* seat is eligible.  This function only
    translates that stable identity into asset-backed GUI steps.  The already
    exercised master route for groups 1..3 retains
    ``#341[位置1] -> #342[占领]``.  Group 4 enters the same native master list
    through the target master's projected ``minesCoord`` instead; this avoids
    reusing an unrelated asset coordinate on the 福地 layout.  Followers 4..12
    continue to project their native ``minesCoord`` onto the #341 map.

    The return value is a preview, not click authority.  A caller must prove
    the live map is at the calibrated scroll offset.  Non-default resolution
    needs explicit ``origin`` and ``scale``.  Empty and occupied follower
    hitboxes remain separate gates so evidence for one cannot authorize the
    other.
    """

    normalized_viewport = _viewport(viewport)
    if isinstance(group, bool) or not isinstance(group, int) or group not in SUPPORTED_GROUPS:
        raise ValueError("洞天席位只支持普通矿区 group 1..4")
    if isinstance(quality, bool) or not isinstance(quality, int) or quality not in {1, 2}:
        raise ValueError("洞天席位 quality 必须是 1(尊主) 或 2(侍从)")
    if isinstance(seat_id, bool) or not isinstance(seat_id, int):
        raise ValueError("洞天席位 seat_id 必须是整数")
    normalized_occupancy = _normalize_occupancy(occupancy)
    if not isinstance(scroll_offset_verified, bool):
        raise ValueError("洞天席位 scroll_offset_verified 必须是布尔值")

    blockers: list[str] = []
    if not scroll_offset_verified:
        blockers.append("scroll_offset_unverified")

    if quality == 1:
        if seat_id not in {1, 2, 3}:
            raise ValueError("洞天尊主 seat_id 必须是 1..3")
        direct_entry = group in DIRECT_MASTER_ENTRY_GROUPS
        if not direct_entry and (origin is not None or scale is not None):
            raise ValueError("洞天尊主统一入口只消费资产 Shape，不接受 origin/scale")
        if not direct_entry and normalized_viewport != DEFAULT_VIEWPORT:
            blockers.append("nondefault_master_viewport_unverified")
        entry_shape = (
            scene_341_shapes.get(MASTER_SHARED_ENTRY_SHAPE)
            if not direct_entry and isinstance(scene_341_shapes, Mapping)
            else None
        )
        action_shape = (
            scene_342_shapes.get(MASTER_SHARED_ACTION_SHAPE)
            if isinstance(scene_342_shapes, Mapping)
            else None
        )
        if direct_entry:
            geometry = resolve_dongtian_fixed_seat(
                quality,
                seat_id,
                group=group,
                viewport=normalized_viewport,
                origin=origin,
                scale=scale,
            )
            entry_point = geometry.point
        else:
            entry_point = _normalized_shape_center(entry_shape, viewport=normalized_viewport)
        action_point = _normalized_shape_center(action_shape, viewport=normalized_viewport)
        if direct_entry:
            if normalized_occupancy == "empty":
                entry_verified = bool(empty_hitbox_verified)
                if not entry_verified:
                    blockers.append("empty_master_hitbox_unverified")
            elif normalized_occupancy == "occupied":
                entry_verified = bool(occupied_hitbox_verified)
                if not entry_verified:
                    blockers.append("occupied_master_hitbox_unverified")
            else:
                entry_verified = False
                blockers.append("master_occupancy_unknown")
        else:
            entry_verified = bool(
                entry_point is not None and _shape_has_observed_landing(entry_shape, 342)
            )
        action_verified = bool(
            action_point is not None and _shape_has_observed_landing(action_shape, 343)
        )
        if entry_point is None and not direct_entry:
            blockers.append("scene_341_master_entry_shape_missing")
        elif not direct_entry and not entry_verified:
            blockers.append("scene_341_master_entry_landing_unverified")
        if action_point is None:
            blockers.append("scene_342_master_action_shape_missing")
        elif not action_verified:
            blockers.append("scene_342_master_action_landing_unverified")
        if normalized_occupancy != "empty":
            # The shared friendly action does not address an arbitrary occupied
            # master; it searches the native list for the first empty row.
            blockers.append("master_shared_route_requires_empty_target")
        return DongtianSeatGuiRoute(
            quality=quality,
            seat_id=seat_id,
            group=group,
            occupancy=normalized_occupancy,
            ui_route="master_list_first_empty",
            viewport=normalized_viewport,
            steps=(
                DongtianSeatGuiStep(
                    scene_id=341,
                    locator_kind=("projected_point" if direct_entry else "asset_shape"),
                    shape_title=(None if direct_entry else MASTER_SHARED_ENTRY_SHAPE),
                    point=entry_point,
                    expected_scene_ids=(342,),
                    verified_for_click=entry_verified,
                ),
                DongtianSeatGuiStep(
                    scene_id=342,
                    locator_kind="asset_shape",
                    shape_title=MASTER_SHARED_ACTION_SHAPE,
                    point=action_point,
                    expected_scene_ids=(343,),
                    verified_for_click=action_verified,
                ),
            ),
            blockers=tuple(dict.fromkeys(blockers)),
        )

    if seat_id not in ATTENDANT_CONFIG_POINTS:
        raise ValueError("洞天侍从 seat_id 必须是 4..12")
    geometry = resolve_dongtian_attendant_seat(
        seat_id,
        group=group,
        viewport=normalized_viewport,
        origin=origin,
        scale=scale,
    )
    hitbox_verified = False
    if normalized_occupancy == "empty":
        hitbox_verified = bool(empty_hitbox_verified)
        if not hitbox_verified:
            blockers.append("empty_follower_hitbox_unverified")
    elif normalized_occupancy == "occupied":
        hitbox_verified = bool(occupied_hitbox_verified)
        if not hitbox_verified:
            blockers.append("occupied_follower_hitbox_unverified")
    else:
        blockers.append("follower_occupancy_unknown")

    return DongtianSeatGuiRoute(
        quality=quality,
        seat_id=seat_id,
        group=group,
        occupancy=normalized_occupancy,
        ui_route="follower_seat_direct",
        viewport=normalized_viewport,
        steps=(
            DongtianSeatGuiStep(
                scene_id=341,
                locator_kind="projected_point",
                shape_title=None,
                point=geometry.point,
                expected_scene_ids=(343,),
                verified_for_click=hitbox_verified,
            ),
        ),
        blockers=tuple(dict.fromkeys(blockers)),
    )


def resolve_dongtian_target_gui_route(
    target: Mapping[str, Any],
    *,
    group: int,
    viewport: Sequence[int] = DEFAULT_VIEWPORT,
    scene_341_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    scene_342_shapes: Mapping[str, Mapping[str, Any]] | None = None,
    origin: Sequence[Real] | None = None,
    scale: Real | Sequence[Real] | None = None,
    scroll_offset_verified: bool = False,
    occupied_hitbox_verified: bool = OCCUPIED_HITBOX_VERIFIED,
    empty_hitbox_verified: bool = EMPTY_HITBOX_VERIFIED,
) -> DongtianSeatGuiRoute:
    """Resolve a full seating-decision target without weakening its identity.

    ``mode`` determines whether the Runtime target is empty or occupied; GUI
    evidence is not allowed to infer that business fact.  ``seat_key`` and the
    declared ``ui_route`` are cross-checked when present so a stale target
    cannot silently project onto another seat or route family.
    """

    if not isinstance(target, Mapping):
        raise ValueError("洞天 Runtime target 必须是对象")
    quality = target.get("quality")
    seat_id = target.get("seat_id")
    mine_id = target.get("mine_id")
    if any(isinstance(value, bool) for value in (quality, seat_id, mine_id)):
        raise ValueError("洞天 Runtime target 身份字段必须是正整数")
    try:
        normalized_quality = int(quality)
        normalized_seat_id = int(seat_id)
        normalized_mine_id = int(mine_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("洞天 Runtime target 身份字段必须是正整数") from exc
    if normalized_mine_id <= 0:
        raise ValueError("洞天 Runtime target mine_id 必须是正整数")

    mode = str(target.get("mode") or "").strip()
    if mode == "occupy_empty":
        occupancy: SeatOccupancy = "empty"
    elif mode in {"inspect_defender", "refresh_defender", "replace_weaker_enemy"}:
        occupancy = "occupied"
    else:
        occupancy = "unknown"
    route = resolve_dongtian_seat_gui_route(
        quality=normalized_quality,
        seat_id=normalized_seat_id,
        group=group,
        occupancy=occupancy,
        viewport=viewport,
        scene_341_shapes=scene_341_shapes,
        scene_342_shapes=scene_342_shapes,
        origin=origin,
        scale=scale,
        scroll_offset_verified=scroll_offset_verified,
        occupied_hitbox_verified=occupied_hitbox_verified,
        empty_hitbox_verified=empty_hitbox_verified,
    )

    blockers = list(route.blockers)
    expected_key = f"{normalized_mine_id}:{normalized_quality}:{normalized_seat_id}"
    declared_key = str(target.get("seat_key") or "").strip()
    if declared_key != expected_key:
        blockers.append("runtime_seat_key_missing_or_mismatched")
    declared_route = str(target.get("ui_route") or "").strip()
    if declared_route and declared_route != route.ui_route:
        blockers.append("runtime_ui_route_mismatched")
    if normalized_quality == 1 and target.get("friendly_place") is not True:
        blockers.append("master_shared_route_requires_friendly_mine")
    return replace(route, blockers=tuple(dict.fromkeys(blockers)))


__all__ = [
    "ATTENDANT_CONFIG_POINTS",
    "ATTENDANT_VISUAL_ORDER",
    "DEFAULT_ORIGIN",
    "DEFAULT_SCALE",
    "DEFAULT_VIEWPORT",
    "DIRECT_MASTER_ENTRY_GROUPS",
    "DongtianSeatGeometry",
    "DongtianSeatGuiRoute",
    "DongtianSeatGuiStep",
    "EMPTY_HITBOX_VERIFIED",
    "MASTER_SHARED_ACTION_SHAPE",
    "MASTER_SHARED_ENTRY_SHAPE",
    "MASTER_CONFIG_POINTS",
    "MASTER_VISUAL_ORDER",
    "OCCUPIED_HITBOX_VERIFIED",
    "SEAT_CONFIG_POINTS",
    "resolve_dongtian_attendant_seat",
    "resolve_dongtian_fixed_seat",
    "resolve_dongtian_seat_gui_route",
    "resolve_dongtian_target_gui_route",
]
