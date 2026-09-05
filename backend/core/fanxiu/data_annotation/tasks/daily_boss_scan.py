from __future__ import annotations

import re
import unicodedata
from typing import Literal


DAILY_BOSS_FIND_MAX_SCROLLS = 16
DAILY_BOSS_FIND_TIMEOUT_SECONDS = 120.0


def daily_boss_list_page_fingerprint(text: str) -> str:
    """Build a stable semantic fingerprint for one visible boss-list page."""

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = re.sub(r"\s+", "", normalized)
    # Refresh countdowns change every second and must not make a stationary page
    # look new forever. Preserve all other digits because boss levels/IDs often
    # distinguish adjacent pages; erasing every number would create false
    # repeated-page matches.
    normalized = re.sub(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)", "<倒计时>", normalized)
    return re.sub(r"((?:刷新|剩余)(?:时间)?)\d+(?:秒|s)", r"\1<倒计时>", normalized, flags=re.IGNORECASE)


def daily_boss_scan_bound_reason(
    *,
    now: float,
    deadline: float,
    scroll_count: int,
    max_scrolls: int,
    page_fingerprint: str = "",
    seen_fingerprints: set[str] | frozenset[str] = frozenset(),
    before_scroll: bool = False,
) -> Literal["deadline", "repeated_page", "max_scrolls"] | None:
    """Return the fail-closed reason for a bounded watched-boss list scan."""

    if now >= deadline:
        return "deadline"
    if page_fingerprint and page_fingerprint in seen_fingerprints:
        return "repeated_page"
    if before_scroll and scroll_count >= max_scrolls:
        return "max_scrolls"
    return None
