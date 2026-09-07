from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.dongtian_seating_schedule import next_dongtian_seating_at


@pytest.mark.parametrize("current, expected", [
    ("2026-09-06 20:00", "2026-09-07 10:00"),
    ("2026-09-07 09:59", "2026-09-07 10:00"),
    ("2026-09-07 10:00", "2026-09-07 10:30"),
    ("2026-09-07 10:30", "2026-09-07 11:00"),
    ("2026-09-07 11:00", "2026-09-07 12:00"),
    ("2026-09-07 12:00", "2026-09-07 13:00"),
    ("2026-09-07 13:00", "2026-09-07 16:00"),
    ("2026-09-07 16:00", "2026-09-07 20:00"),
    ("2026-09-07 20:00", "2026-09-08 10:00"),
    ("2026-09-08 10:00", "2026-09-09 10:00"),
    ("2026-09-07 10:17", "2026-09-07 10:30"),
])
def test_next_check_preserves_fixed_business_windows(current, expected):
    assert next_dongtian_seating_at(datetime.fromisoformat(current)) == datetime.fromisoformat(expected)


def test_initial_creation_can_include_exact_trigger():
    at = datetime(2026, 9, 7, 10)
    assert next_dongtian_seating_at(at, include_current=True) == at
