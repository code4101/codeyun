from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from backend.core.fanxiu.game.ocr_utils import _sanitize_ocr_text


GONGFA_CATEGORY_OPTIONS = ("全部", "仙术", "剑修", "法修", "魔修", "体修")
_EXPECTED_CHARACTERS = tuple("".join(GONGFA_CATEGORY_OPTIONS))


@dataclass(frozen=True)
class OcrCharacter:
    text: str
    x: float
    y: float
    w: float
    h: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


@dataclass(frozen=True)
class GongfaCategoryOption:
    label: str
    characters: tuple[OcrCharacter, OcrCharacter]

    @property
    def click_point(self) -> tuple[float, float]:
        """Return the center between the two visible character centers."""

        first_x, first_y = self.characters[0].center
        second_x, second_y = self.characters[1].center
        return (first_x + second_x) / 2, (first_y + second_y) / 2


def _token_characters(token: dict[str, Any]) -> list[OcrCharacter]:
    """Expand one OCR token into approximate per-character boxes."""

    text = _sanitize_ocr_text(str(token.get("text") or ""))
    characters = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    if not characters:
        return []

    x = float(token.get("x") or 0)
    y = float(token.get("y") or 0)
    w = float(token.get("w") or 0)
    h = float(token.get("h") or 0)
    char_width = w / len(characters)
    return [
        OcrCharacter(
            text=char,
            x=x + index * char_width,
            y=y,
            w=char_width,
            h=h,
        )
        for index, char in enumerate(characters)
    ]


def locate_gongfa_category_options(
    tokens: Iterable[dict[str, Any]],
) -> tuple[GongfaCategoryOption, ...]:
    """Locate the six fixed #439 category options from character OCR boxes.

    The caller should pass only the cropped OCR tokens from #439「选项」.
    Characters are ordered by their horizontal center, paired from left to
    right, and checked against the fixed UI labels before any point is used.
    """

    characters = [
        character
        for token in tokens
        for character in _token_characters(dict(token))
    ]
    characters.sort(key=lambda item: (item.center[0], item.center[1]))

    observed = tuple(character.text for character in characters)
    if observed != _EXPECTED_CHARACTERS:
        raise ValueError(
            "#439「选项」单字序列不完整或顺序异常；"
            f"期望={''.join(_EXPECTED_CHARACTERS)!r}，实际={''.join(observed)!r}"
        )

    return tuple(
        GongfaCategoryOption(
            label=GONGFA_CATEGORY_OPTIONS[index],
            characters=(characters[index * 2], characters[index * 2 + 1]),
        )
        for index in range(len(GONGFA_CATEGORY_OPTIONS))
    )


def gongfa_category_click_point(
    tokens: Iterable[dict[str, Any]],
    target: str,
) -> tuple[float, float]:
    """Return a validated click point for one #439 category label."""

    for option in locate_gongfa_category_options(tokens):
        if option.label == target:
            return option.click_point
    raise ValueError(
        f"未知功法分类 {target!r}；可选项={list(GONGFA_CATEGORY_OPTIONS)}"
    )
