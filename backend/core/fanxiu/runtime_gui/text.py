from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable


DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD = 0.62


@dataclass(frozen=True)
class OcrNameMatch:
    expected: str
    observed: str
    normalized_expected: str
    normalized_observed: str
    similarity: float
    index: int
    passed_threshold: bool


def normalize_ocr_name(value: Any) -> str:
    """Normalize a business/player name to the glyph stream OCR can return."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[丶、·•]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _edit_similarity(left: str, right: str) -> float:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


def ocr_name_similarity(expected: Any, observed: Any) -> float:
    """Score an expected name against a possibly noisy OCR text fragment."""

    expected_key = normalize_ocr_name(expected)
    observed_key = normalize_ocr_name(observed)
    if not expected_key or not observed_key:
        return 0.0
    if expected_key in observed_key:
        return 1.0

    best = 0.0
    min_window = max(1, len(expected_key) - 1)
    max_window = min(len(observed_key), len(expected_key) + 1)
    for width in range(min_window, max_window + 1):
        for start in range(0, len(observed_key) - width + 1):
            best = max(best, _edit_similarity(expected_key, observed_key[start:start + width]))
    return round(best, 6)


def rank_ocr_name_matches(
    expected: Any,
    observed_names: Iterable[Any],
    *,
    threshold: float = DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
) -> list[OcrNameMatch]:
    """Rank every non-empty OCR candidate; below-threshold names remain usable."""

    expected_text = str(expected or "")
    expected_key = normalize_ocr_name(expected_text)
    minimum = min(1.0, max(0.0, float(threshold)))
    matches: list[OcrNameMatch] = []
    for index, observed in enumerate(observed_names):
        observed_text = str(observed or "")
        observed_key = normalize_ocr_name(observed_text)
        if not observed_key:
            continue
        similarity = ocr_name_similarity(expected_text, observed_text)
        matches.append(OcrNameMatch(
            expected=expected_text,
            observed=observed_text,
            normalized_expected=expected_key,
            normalized_observed=observed_key,
            similarity=similarity,
            index=index,
            passed_threshold=similarity >= minimum,
        ))
    return sorted(matches, key=lambda match: (-match.similarity, match.index))


def best_ocr_name_match(
    expected: Any,
    observed_names: Iterable[Any],
    *,
    threshold: float = DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD,
) -> OcrNameMatch | None:
    """Return the highest-similarity name, even when every score is below threshold."""

    ranked = rank_ocr_name_matches(expected, observed_names, threshold=threshold)
    return ranked[0] if ranked else None


__all__ = [
    "DEFAULT_OCR_NAME_SIMILARITY_THRESHOLD",
    "OcrNameMatch",
    "best_ocr_name_match",
    "normalize_ocr_name",
    "ocr_name_similarity",
    "rank_ocr_name_matches",
]
