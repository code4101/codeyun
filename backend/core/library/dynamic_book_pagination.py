from __future__ import annotations

import math
import re

from bs4 import BeautifulSoup, Tag


DYNAMIC_BOOK_CHARACTERS_PER_PAGE = 1000
IMAGE_TILE_SIZE = 512
IMAGE_HIGH_DETAIL_SHORT_SIDE = 768
IMAGE_MAX_SIDE = 2048
IMAGE_BASE_TOKENS = 70
IMAGE_TILE_TOKENS = 140
IMAGE_TOKEN_TO_CHARACTER_RATIO = 0.65
SMALL_IMAGE_CHARACTER_SIDE = 16
INLINE_MARKER_EQUIVALENT_CHARACTERS = 1
INLINE_MARKER_IMAGE_PATTERN = re.compile(
    r"(?:^|[\s_-])(footnote|icon|emoji|badge|avatar)(?:$|[\s_-])",
    re.I,
)


def estimate_image_equivalent_characters(
    source_width: float,
    source_height: float,
    *,
    page_characters: int = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
) -> int:
    """Convert one image to a calibrated, OpenAI-tile-inspired reading weight."""
    width = max(1.0, float(source_width))
    height = max(1.0, float(source_height))
    if max(width, height) <= 128:
        equivalent = math.floor(
            width * height / SMALL_IMAGE_CHARACTER_SIDE**2 + 0.5
        )
        return min(max(1, equivalent), max(1, int(page_characters)))
    fit_scale = min(1.0, IMAGE_MAX_SIDE / max(width, height))
    width *= fit_scale
    height *= fit_scale

    if width * height > IMAGE_TILE_SIZE**2:
        detail_scale = IMAGE_HIGH_DETAIL_SHORT_SIDE / min(width, height)
        width *= detail_scale
        height *= detail_scale

    tiles = math.ceil(width / IMAGE_TILE_SIZE) * math.ceil(height / IMAGE_TILE_SIZE)
    equivalent = math.floor(
        (IMAGE_BASE_TOKENS + IMAGE_TILE_TOKENS * tiles)
        * IMAGE_TOKEN_TO_CHARACTER_RATIO
        + 0.5
    )
    return min(
        max(1, equivalent),
        max(1, int(page_characters)),
    )


def _positive_dimension(value: object) -> float | None:
    match = re.match(r"^\s*([\d.]+)(?:px)?\s*$", str(value or ""), re.I)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if parsed > 0 else None


def _image_dimensions(image: Tag) -> tuple[float, float]:
    width = _positive_dimension(image.get("width"))
    height = _positive_dimension(image.get("height"))
    style = str(image.get("style") or "")
    if width is None:
        match = re.search(r"(?:^|;)\s*width\s*:\s*([\d.]+)px", style, re.I)
        width = _positive_dimension(match.group(1) if match else None)
    if height is None:
        match = re.search(r"(?:^|;)\s*height\s*:\s*([\d.]+)px", style, re.I)
        height = _positive_dimension(match.group(1) if match else None)
    if width is not None and height is not None:
        return width, height
    if width is not None:
        return width, width * 2 / 3
    if height is not None:
        return height * 3 / 2, height
    return 768, 512


def _is_inline_marker_image(image: Tag) -> bool:
    if image.find_parent(["sup", "sub"]) is not None:
        return True
    parent = image.parent if isinstance(image.parent, Tag) else None
    descriptor = " ".join([
        " ".join(image.get("class") or []),
        " ".join(parent.get("class") or []) if parent is not None else "",
        str(image.get("role") or ""),
    ])
    return INLINE_MARKER_IMAGE_PATTERN.search(descriptor) is not None


def dynamic_book_html_character_count(html: str) -> int:
    """Count normalized text plus image-equivalent characters in rich HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    text_count = len(re.sub(r"\s+", " ", soup.get_text("", strip=False)).strip())
    image_count = 0
    for image in soup.find_all("img"):
        if _is_inline_marker_image(image):
            image_count += INLINE_MARKER_EQUIVALENT_CHARACTERS
            continue
        width, height = _image_dimensions(image)
        image_count += estimate_image_equivalent_characters(width, height)
    return text_count + image_count


def dynamic_book_html_page_count(html: str) -> int:
    """Estimate whole-book thickness, including image information.

    Interactive reading pages are split by text density in the frontend. This
    aggregate estimate intentionally keeps image weight so illustrated books do
    not appear artificially thin on the shelf.
    """
    return dynamic_book_page_count(dynamic_book_html_character_count(html))


def dynamic_book_page_count(
    text_or_length: str | int,
    page_characters: int = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
) -> int:
    """Return the unified page count for a dynamic book."""
    character_count = len(text_or_length) if isinstance(text_or_length, str) else int(text_or_length)
    return max(1, math.ceil(max(0, character_count) / max(1, int(page_characters))))


def dynamic_book_page_at(
    character_offset: int,
    page_characters: int = DYNAMIC_BOOK_CHARACTERS_PER_PAGE,
) -> int:
    """Map a zero-based character offset to a one-based dynamic-book page."""
    return max(0, int(character_offset)) // max(1, int(page_characters)) + 1
