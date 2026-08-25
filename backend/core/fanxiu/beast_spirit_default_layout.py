from __future__ import annotations

import time
from typing import Any, Iterable, Sequence

from sqlmodel import Session

from backend.db import engine
from backend.models import AppSetting


BEAST_SPIRIT_DEFAULT_LAYOUT_KEY = "fanxiu.beast_spirit.public_default_layout"


def _shape_code(cells: Iterable[Sequence[int]]) -> str:
    points = [(int(cell[0]), int(cell[1])) for cell in cells]
    if not points:
        raise ValueError("兽魂默认布局包含空形状")
    min_row = min(row for row, _column in points)
    min_column = min(column for _row, column in points)
    normalized = [(row - min_row, column - min_column) for row, column in points]
    height = max(row for row, _column in normalized) + 1
    width = max(column for _row, column in normalized) + 1
    matrix = [["0"] * width for _ in range(height)]
    for row, column in normalized:
        matrix[row][column] = "1"
    return ";".join("".join(row) for row in matrix)


def build_beast_spirit_default_layout(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project a verified game snapshot into the public calculator baseline."""

    layout = snapshot.get("layout") if isinstance(snapshot.get("layout"), dict) else {}
    if not snapshot.get("complete") or not layout:
        raise ValueError("兽魂快照不完整，不能发布默认布局")
    if int(layout.get("score_gain") or 0) != 0:
        raise ValueError("兽魂镶嵌盘尚未达到最优布局，不能发布默认布局")

    item_by_id = {
        str(item.get("item_id")): item
        for item in snapshot.get("items") or []
        if item.get("item_id") is not None
    }
    ordered_roles: list[tuple[str, str]] = []
    ordered_roles.extend(
        (str(item_id), "high_prefix")
        for item_id in layout.get("high_prefix_item_ids") or []
    )
    reserves = layout.get("low_level_reserves") or {}
    ordered_roles.extend(
        (str(item_id), "level1_single")
        for item_id in reserves.get("single_item_ids") or []
    )
    if reserves.get("horizontal_item_id"):
        ordered_roles.append((str(reserves["horizontal_item_id"]), "level2_horizontal"))
    if reserves.get("vertical_item_id"):
        ordered_roles.append((str(reserves["vertical_item_id"]), "level2_vertical"))

    published_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item_id, role in ordered_roles:
        if item_id in seen_ids:
            continue
        item = item_by_id.get(item_id)
        if item is None:
            raise ValueError(f"兽魂默认布局找不到候选 {item_id}")
        seen_ids.add(item_id)
        published_items.append(
            {
                "item_id": item_id,
                "role": role,
                "level": int(item.get("level") or 0),
                "shape_id": int(item.get("shape_id") or 0),
                "code": _shape_code(item.get("shape") or []),
                "score": int(item.get("score") or 0),
            }
        )

    if not published_items:
        raise ValueError("兽魂默认布局没有可发布候选")
    published_at = time.time()
    return {
        "version": str(time.time_ns()),
        "published_at": published_at,
        "captured_at": snapshot.get("captured_at"),
        "rows": 5,
        "cols": 6,
        "protected_prefix_k": int(layout.get("protected_prefix_k") or 0),
        "protected_prefix_m": int(layout.get("protected_prefix_m") or 0),
        "optimal_score": int(layout.get("score") or 0),
        "items": published_items,
    }


def save_beast_spirit_default_layout(
    snapshot: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    payload = build_beast_spirit_default_layout(snapshot)

    def save(target: Session) -> None:
        row = target.get(AppSetting, BEAST_SPIRIT_DEFAULT_LAYOUT_KEY)
        if row is None:
            row = AppSetting(key=BEAST_SPIRIT_DEFAULT_LAYOUT_KEY)
        row.value = payload
        row.updated_at = float(payload["published_at"])
        target.add(row)
        target.commit()

    if session is not None:
        save(session)
    else:
        with Session(engine) as target:
            save(target)
    return payload


def read_beast_spirit_default_layout(session: Session) -> dict[str, Any] | None:
    row = session.get(AppSetting, BEAST_SPIRIT_DEFAULT_LAYOUT_KEY)
    if row is None or not isinstance(row.value, dict) or not row.value.get("items"):
        return None
    return dict(row.value)


__all__ = [
    "BEAST_SPIRIT_DEFAULT_LAYOUT_KEY",
    "build_beast_spirit_default_layout",
    "read_beast_spirit_default_layout",
    "save_beast_spirit_default_layout",
]
