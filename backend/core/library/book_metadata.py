from __future__ import annotations

import calendar
import re
from datetime import date


BOOK_START_DATE_PATTERN = re.compile(r"^\d{4}(?:-\d{2}(?:-\d{2})?)?$")


def normalize_book_start_date(value: str | None) -> str:
    """Normalize a book's earliest publication date without inventing precision."""
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if not BOOK_START_DATE_PATTERN.fullmatch(normalized):
        raise ValueError("起始时间须为 YYYY、YYYY-MM 或 YYYY-MM-DD")

    parts = normalized.split("-")
    year = int(parts[0])
    if year < 1:
        raise ValueError("起始时间年份须介于 0001 和 9999")
    if len(parts) >= 2:
        month = int(parts[1])
        if not 1 <= month <= 12:
            raise ValueError("起始时间月份须介于 01 和 12")
    if len(parts) == 3:
        day = int(parts[2])
        if not 1 <= day <= calendar.monthrange(year, month)[1]:
            raise ValueError("起始时间日期无效")
        date(year, month, day)
    return normalized
