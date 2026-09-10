from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, TypeVar


def parse_ocr_values(
    text: Any,
    *,
    expected_count: int | None = None,
    allow_extra_numbers: bool = False,
) -> tuple[int, ...] | None:
    r"""Extract an ordered group of integer values from OCR text.

    This is the single bottom-level parser for Fanxiu OCR numeric values.  It
    extracts ``\d+`` groups and deliberately ignores *every* separator because
    OCR may turn ``/`` into ``丨``, ``｜``, whitespace, or a newline.
    One value is therefore as valid as several values.

    ``expected_count`` expresses business meaning, not punctuation.  For
    example, a caller discussing numerator/denominator passes
    ``expected_count=2``; a single counter passes ``expected_count=1``; and a
    caller wanting every recognized value leaves it unset.  A count mismatch
    returns ``None``.  Proven broad OCR rows may opt into taking the final
    ``expected_count`` values with ``allow_extra_numbers=True``.
    """

    if expected_count is not None and expected_count < 1:
        raise ValueError("expected_count must be a positive integer or None")
    if allow_extra_numbers and expected_count is None:
        raise ValueError("allow_extra_numbers requires expected_count")

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    groups = re.findall(r"[0-9]+", normalized)
    if not groups:
        return None
    if expected_count is not None and len(groups) != expected_count:
        if not allow_extra_numbers or len(groups) < expected_count:
            return None
        groups = groups[-expected_count:]
    return tuple(int(group) for group in groups)


def parse_ocr_fraction_numbers(
    text: Any,
    *,
    allow_extra_numbers: bool = False,
) -> tuple[int, int] | None:
    """Compatibility wrapper; new code should call :func:`parse_ocr_values`.

    "Fraction" only means that the numeric group must contain exactly two
    values.  No separator is required or interpreted.
    """

    values = parse_ocr_values(
        text,
        expected_count=2,
        allow_extra_numbers=allow_extra_numbers,
    )
    if values is None:
        return None
    numerator, denominator = values
    if denominator <= 0:
        return None
    return numerator, denominator


NumericValue = TypeVar("NumericValue")


def retry_numeric_ocr(
    read_text: Callable[[], str],
    parse_value: Callable[[str], NumericValue | None],
    *,
    wait: Callable[[float], None],
    max_attempts: int = 5,
    retry_interval: float = 2.0,
    on_retry: Callable[[int, str], None] | None = None,
) -> tuple[NumericValue | None, str]:
    """Bounded read-only retry policy; only None means unreadable, never zero.

    The reader supplies a fresh observation for each attempt. Parsing, capture,
    interruption and other exceptions propagate immediately. Exhaustion returns
    None plus the last text so the existing business failure policy stays intact.
    No gameplay action is replayed by this helper.
    """
    if max_attempts < 1 or retry_interval < 0:
        raise ValueError("OCR retries require max_attempts >= 1 and retry_interval >= 0")
    text = ""
    for attempt in range(1, max_attempts + 1):
        text = str(read_text() or "")
        value = parse_value(text)
        if value is not None:
            return value, text
        if attempt < max_attempts:
            if on_retry is not None:
                on_retry(attempt, text)
            wait(retry_interval)
    return None, text
