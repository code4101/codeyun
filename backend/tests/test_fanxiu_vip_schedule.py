"""VIP 下次调度遵循 Cell 业务时间，不访问游戏。"""
import pytest

from backend.core.fanxiu.data_annotation.effective_time import job_effective_time
from backend.core.fanxiu.data_annotation.tasks.vip import DailyVipTaskMixin


@pytest.mark.parametrize(("current", "expected"), [
    ("2026-09-26 00:00:00", "2026-09-27 00:00:00"),
    ("2026-09-26 23:59:59", "2026-09-27 00:00:00"),
    ("2026-12-31 23:59:59", "2027-01-01 00:00:00"),
    ("2028-02-28 12:00:00", "2028-02-29 00:00:00"),
    ("2028-02-29 12:00:00", "2028-03-01 00:00:00"),
])
def test_vip_next_midnight_uses_job_business_clock(current, expected):
    with job_effective_time({"effective_now": current}):
        assert DailyVipTaskMixin()._next_daily_vip_reset_time_text() == expected
