from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name


@dataclass(frozen=True)
class MagicInvasionBottomTabTarget:
    text: str
    x: float
    y: float
    score: float


def resolve_magic_invasion_bottom_tab(
    lines: Iterable[dict[str, Any]],
    *,
    tab_name: str,
    frame_width: float,
    frame_height: float,
) -> MagicInvasionBottomTabTarget:
    """Resolve a vertical bottom activity tab and reject title OCR collisions."""

    expected = normalize_ocr_name(tab_name)
    candidates: list[MagicInvasionBottomTabTarget] = []
    for raw in lines:
        text = str(raw.get("text") or "").strip()
        normalized = normalize_ocr_name(text)
        if not normalized or expected not in normalized:
            continue
        x = float(raw.get("x") or 0)
        y = float(raw.get("y") or 0)
        width = float(raw.get("w") or 0)
        height = float(raw.get("h") or 0)
        if y < float(frame_height) * 0.70 or height <= width * 1.25:
            continue
        if not (0 <= x <= frame_width and 0 <= y <= frame_height):
            continue
        candidates.append(
            MagicInvasionBottomTabTarget(
                text=text,
                x=x + width / 2,
                y=y + height / 2,
                score=float(raw.get("score") or 0),
            )
        )
    if len(candidates) != 1:
        raise RuntimeError(
            f"魔道入侵底部页签 {tab_name!r} 命中 {len(candidates)} 个，拒绝猜测"
        )
    return candidates[0]


__all__ = [
    "MagicInvasionBottomTabTarget",
    "resolve_magic_invasion_bottom_tab",
]
