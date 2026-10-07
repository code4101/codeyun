from datetime import datetime, timedelta, timezone

import pytest

from backend.core.fanxiu.data_annotation.tasks.pet_resource_use import require_pet_resource_window


def test_real_scoring_window_includes_start_and_excludes_end():
    start = datetime(2026, 10, 7, 5, 0, 5, tzinfo=timezone(timedelta(hours=8)))
    end = start.replace(hour=22, minute=0, second=0)
    for current in (start, end - timedelta(microseconds=1), start.astimezone(timezone.utc)):
        require_pet_resource_window(start_at=start, end_at=end, now=current)
    for current in (start - timedelta(microseconds=1), end, end + timedelta(hours=1)):
        with pytest.raises(RuntimeError):
            require_pet_resource_window(start_at=start, end_at=end, now=current)
    with pytest.raises(ValueError):
        require_pet_resource_window(start_at=start.replace(tzinfo=None), end_at=end, now=start)
    with pytest.raises(ValueError):
        require_pet_resource_window(start_at=end, end_at=start, now=start)
