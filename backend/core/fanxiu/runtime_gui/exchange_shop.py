from __future__ import annotations

"""Pure OCR-to-row alignment for the common activity exchange shop."""

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from backend.core.fanxiu.runtime_gui.text import normalize_ocr_name


@dataclass(frozen=True)
class ExchangeShopItemTarget:
    """One uniquely aligned product-name click target."""

    x: float
    y: float
    row_index: int
    observed_name: str
    current_unit_price: int | None


def _number(value: Any) -> int | None:
    text = normalize_ocr_name(value)
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def _bounds(box: Mapping[str, Any]) -> tuple[float, float, float, float]:
    left = float(box["x"])
    top = float(box["y"])
    return left, top, left + float(box["w"]), top + float(box["h"])


def _line_bounds(line: Mapping[str, Any]) -> tuple[float, float, float, float]:
    left = float(line.get("x") or 0)
    top = float(line.get("y") or 0)
    return left, top, left + float(line.get("w") or 0), top + float(line.get("h") or 0)


def _contained(line: Mapping[str, Any], box: Mapping[str, Any]) -> bool:
    line_left, line_top, line_right, line_bottom = _line_bounds(line)
    left, top, right, bottom = _bounds(box)
    return (
        left <= line_left
        and line_right <= right
        and top <= line_top
        and line_bottom <= bottom
    )


def resolve_exchange_shop_item(
    lines: Iterable[Mapping[str, Any]],
    *,
    product_list_box: Mapping[str, Any],
    product_row_boxes: Sequence[Mapping[str, Any]],
    expected_name: str,
    expected_unit_price: int | None = None,
) -> ExchangeShopItemTarget:
    """Resolve one exact product row, failing closed on missing or ambiguous evidence.

    Product names are compared after OCR-name normalization.  When duplicate
    names are visible, the expected unit price is compared only with the
    left-side numeric value below that row's name.  A crossed-out original
    price rendered in the right half of the row is never accepted as current.
    """

    grouped_lines = list(lines)
    expected_key = normalize_ocr_name(expected_name)
    if not expected_key:
        raise RuntimeError("兑换商品名不能为空")
    if not product_row_boxes:
        raise RuntimeError("兑换商品行坐标为空")

    candidates: list[tuple[Mapping[str, Any], int, Mapping[str, Any]]] = []
    for line in grouped_lines:
        if normalize_ocr_name(line.get("text")) != expected_key:
            continue
        if not _contained(line, product_list_box):
            continue
        _, line_top, _, line_bottom = _line_bounds(line)
        center_y = (line_top + line_bottom) / 2
        matching_rows = [
            (index, row_box)
            for index, row_box in enumerate(product_row_boxes, start=1)
            if _bounds(row_box)[1] <= center_y <= _bounds(row_box)[3]
        ]
        if len(matching_rows) == 1:
            row_index, row_box = matching_rows[0]
            candidates.append((line, row_index, row_box))

    aligned: list[tuple[Mapping[str, Any], int, int | None]] = []
    for line, row_index, row_box in candidates:
        current_price: int | None = None
        if expected_unit_price is not None:
            name_left, name_top, name_right, name_bottom = _line_bounds(line)
            del name_left, name_top, name_right
            row_left, _, row_right, _ = _bounds(row_box)
            row_midpoint = (row_left + row_right) / 2
            price_lines = []
            for price_line in grouped_lines:
                price = _number(price_line.get("text"))
                price_left, price_top, price_right, _ = _line_bounds(price_line)
                if (
                    price is not None
                    and _contained(price_line, row_box)
                    and price_top > name_bottom
                    and (price_left + price_right) / 2 <= row_midpoint
                ):
                    price_lines.append((price_left, price))
            if not price_lines:
                continue
            # The discounted/current value is the leftmost eligible price.
            # Right-column crossed-out originals were excluded above.
            current_price = min(price_lines, key=lambda item: item[0])[1]
            if current_price != int(expected_unit_price):
                continue
        aligned.append((line, row_index, current_price))

    if len(aligned) != 1:
        raise RuntimeError(
            f"兑换商品 {expected_name} 唯一命中数为 {len(aligned)}"
        )

    line, row_index, current_price = aligned[0]
    left, top, right, bottom = _line_bounds(line)
    return ExchangeShopItemTarget(
        x=(left + right) / 2,
        y=(top + bottom) / 2,
        row_index=row_index,
        observed_name=str(line.get("text") or ""),
        current_unit_price=current_price,
    )
