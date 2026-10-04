"""日常_祈愿周日收尾的业务时间；独立于养成集的每日凭证。"""
from datetime import datetime

from backend.core.fanxiu.data_annotation.job_times import next_business_time


def next_prayer_closeout_time(now: datetime | None = None) -> str:
    """每周日23点；成功后推进到下一周，失败仍由 Scheduler 技术重试。"""
    return next_business_time(("23:00",), weekdays=(6,), now=now)
