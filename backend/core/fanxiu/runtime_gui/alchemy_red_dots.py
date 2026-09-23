"""Read the visible category markers on the native alchemy selection page.

The client computes a category marker and renders it beside the category tab.
This adapter only identifies that already-rendered marker.  It does not infer
craftability from recipes or inventory, and it never reads the game Runtime.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image


# Scene #625, 900 x 1600 reference.  All ten categories fit on this page.
_REFERENCE_SIZE = (900, 1600)
_CATEGORY_ROWS = (
    (2, "筑基", 280),
    (3, "结丹", 382),
    (4, "元婴", 485),
    (5, "化神", 588),
    (6, "炼虚", 690),
    (7, "合体", 791),
    (8, "大乘", 892),
    (10, "真仙", 995),
    (9, "祈愿", 1096),
    (15, "仙药", 1197),
)


def read_alchemy_category_red_dots(image_bytes: bytes) -> list[dict[str, object]]:
    """Return top-to-bottom red marker states from a ready #625 frame.

    Caller must first identify scene #625.  A red marker occupies the narrow
    strip to the right of each category label; sampling only that strip avoids
    the recipe cards and unrelated red pixels elsewhere on the screen.
    """

    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
    width, height = image.size
    sx, sy = width / _REFERENCE_SIZE[0], height / _REFERENCE_SIZE[1]
    rows: list[dict[str, object]] = []
    for type_id, name, center_y in _CATEGORY_ROWS:
        box = (
            round(192 * sx),
            round((center_y - 20) * sy),
            round(222 * sx),
            round((center_y + 20) * sy),
        )
        pixels = image.crop(box).getdata()
        red_count = sum(
            1
            for red, green, blue in pixels
            if red > 140
            and 35 < green < 180
            and red > green * 1.35
            and red > blue * 1.35
        )
        pixel_count = (box[2] - box[0]) * (box[3] - box[1])
        rows.append(
            {
                "type_id": type_id,
                "name": name,
                "red": red_count >= max(12, round(pixel_count * 0.04)),
                "red_pixels": red_count,
            }
        )
    return rows


def first_alchemy_category_red_dot(image_bytes: bytes) -> dict[str, object] | None:
    """Select the first rendered category red dot in native display order."""

    return next((row for row in read_alchemy_category_red_dots(image_bytes) if row["red"]), None)
