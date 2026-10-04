"""祈愿周日收尾的时间与完成凭证纯契约，不模拟游戏操作。"""
from datetime import datetime

import pytest

from backend.core.fanxiu.data_annotation.tasks.prayer_sunday_closeout import next_prayer_closeout_time


@pytest.mark.parametrize("moment,expected", [
    ("2026-10-03 23:00:00", "2026-10-04 23:00:00"),
    ("2026-10-04 00:00:00", "2026-10-04 23:00:00"),
    ("2026-10-04 22:59:59", "2026-10-04 23:00:00"),
    ("2026-10-04 23:00:00", "2026-10-11 23:00:00"),
    ("2026-10-04 23:59:59", "2026-10-11 23:00:00"),
    ("2026-10-05 00:00:00", "2026-10-11 23:00:00"),
])
def test_next_daily_and_sunday_closeout_trigger(moment, expected):
    assert next_prayer_closeout_time(datetime.fromisoformat(moment)) == expected


def test_independent_closeout_registered_and_daily_prayer_remains_internal():
    from backend.core.fanxiu.data_annotation.default_jobs import register_fanxiu_default_jobs
    from backend.core.fanxiu.data_annotation.jobs import get_fanxiu_data_annotation_task_cell_definition
    from backend.core.fanxiu.data_annotation.kernel_scheduler_defaults import default_kernel_scheduler_tasks
    register_fanxiu_default_jobs()
    task = next(t for t in default_kernel_scheduler_tasks(datetime(2026, 10, 4, 16))
                if t['id'] == 'daily-prayer-closeout')
    assert task['next_time'] == '2026-10-04 23:00:00'
    assert task['label'] == '日常_祈愿周日收尾'
    assert get_fanxiu_data_annotation_task_cell_definition('daily_prayer_closeout').scheduler_supported
    assert not get_fanxiu_data_annotation_task_cell_definition('prayer_update').scheduler_supported
