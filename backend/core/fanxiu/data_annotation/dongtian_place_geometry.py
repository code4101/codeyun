"""Map fixed MinesPlace coordinates into the currently scrolled Dongtian viewport.

Geometry guides scrolling only. A predicted point is never a click authorization:
the caller must observe the target's own OCR outside fixed UI overlays, then
verify the destination page and fresh Runtime identity after clicking.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Callable, Mapping, Sequence
import re


def normalize_dongtian_place_name(value: Any) -> str:
    """Strip the bracketed tier, not arbitrary neighboring UI text."""
    return re.sub(r"\s+", "", re.sub(r"^[\[【][^\]】]+[\]】]", "", str(value or "")))


def resolve_dongtian_ocr_name(value: Any, names: Sequence[str]) -> str | None:
    """Unique standalone name with at most one OCR edit; ambiguity rejects.

    Two-character truncations such as 紫琅 are allowed only when unique in the
    full catalog. 月虹 cannot choose between 月虹梁 and 月虹窟. This resolves
    identity, not click permission: a fuzzy match also needs geometry support.
    """
    observed = normalize_dongtian_place_name(value)
    canonical = {normalize_dongtian_place_name(name): name for name in names}
    if observed in canonical:
        return canonical[observed]
    if len(observed) < 2:
        return None
    matches = []
    for name, original in canonical.items():
        if abs(len(observed) - len(name)) > 1:
            continue
        row = list(range(len(name) + 1))
        for i, char in enumerate(observed, 1):
            new = [i]
            for j, other in enumerate(name, 1):
                new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (char != other)))
            row = new
        if row[-1] <= 1:
            matches.append(original)
    return matches[0] if len(matches) == 1 else None


def dongtian_label_positions(configs: Sequence[Mapping[str, Any]]) -> dict[str, tuple[float, float]]:
    """Config coordinates corrected to native title anchors, not building art.

    Real #279 multi-tier frames on 2026-09-21 show title offsets of about
    126/50 screen pixels at scale 1.2 for 白玉京/group2. Ordinary group3/4
    share the base prefab. Units here are map units, so resizing stays linear.
    Separate 灵台妙境 has no map position and is deliberately excluded.
    """
    offsets = {1: 105.0, 2: 42.0}
    return {
        normalize_dongtian_place_name(p["name"]):
        (float(p["pos"][0]), float(p["pos"][1]) - offsets.get(int(p.get("group") or 0), 0.0))
        for p in configs if len(p.get("pos") or []) >= 2
    }


def estimate_dongtian_target_center(
    target_name: str,
    lines: Sequence[Mapping[str, Any]],
    places: Mapping[str, tuple[float, float]],
    normalize: Callable[[Any], str],
) -> tuple[float, float] | None:
    """Fit one isotropic map scale and translation from >=2 visible place names.

    MinesPlace ``pos`` is fixed: screen_x = ox + scale*x and
    screen_y = oy - scale*y. OCR may split or miss a place; only exact,
    unambiguous place-name lines enter the fit. A large residual rejects an
    animation frame or a false OCR anchor instead of yielding a guessed click.
    """

    target = places.get(normalize(target_name))
    if target is None:
        return None
    anchors: list[tuple[float, float, float, float]] = []
    for line in lines:
        world = places.get(normalize(line.get("text")))
        if world is None:
            continue
        try:
            x = float(line["x"]) + float(line["w"]) / 2
            y = float(line["y"]) + float(line["h"]) / 2
        except (KeyError, TypeError, ValueError):
            continue
        anchors.append((world[0], world[1], x, y))
    if len(anchors) < 2:
        return None
    scales: list[float] = []
    for i, (wx, wy, sx, sy) in enumerate(anchors):
        for vx, vy, tx, ty in anchors[i + 1 :]:
            if abs(wx - vx) >= 80:
                scales.append((sx - tx) / (wx - vx))
            if abs(wy - vy) >= 80:
                scales.append(-(sy - ty) / (wy - vy))
    scales = [value for value in scales if 0.6 <= value <= 2.0]
    if not scales:
        return None
    scale = median(scales)
    ox = median(sx - scale * wx for wx, _wy, sx, _sy in anchors)
    oy = median(sy + scale * wy for _wx, wy, _sx, sy in anchors)
    inliers = sum(
        abs(sx - (ox + scale * wx)) <= 35
        and abs(sy - (oy - scale * wy)) <= 35
        for wx, wy, sx, sy in anchors
    )
    if inliers < 2 or inliers < len(anchors) - 1:
        return None
    return ox + scale * target[0], oy - scale * target[1]


def dongtian_geometry_scroll_direction(
    point: tuple[float, float],
    window: Mapping[str, Any],
    roster: Mapping[str, Any],
    *,
    margin: float = 55.0,
) -> str | None:
    """Move a hidden target into the unobstructed OCR band; never click it."""

    x, y = point
    top = float(window["y"]) + margin
    bottom = float(window["y"]) + float(window["h"]) - margin
    if y < top:
        return "up"  # Finger down moves map content down.
    if y > bottom:
        return "down"  # Finger up moves map content up.
    left = float(roster["x"])
    right = left + float(roster["w"])
    roster_top = float(roster["y"])
    roster_bottom = roster_top + float(roster["h"])
    if left - margin <= x <= right + margin and roster_top - margin <= y <= roster_bottom + margin:
        # Always reveal the title below the fixed roster. Sliding it just
        # above the panel leaves it clipped by the header and oscillates with
        # the off-screen correction on the next tick.
        return "up"
    return None


__all__ = [
    "estimate_dongtian_target_center", "dongtian_geometry_scroll_direction",
    "normalize_dongtian_place_name", "resolve_dongtian_ocr_name", "dongtian_label_positions",
]
