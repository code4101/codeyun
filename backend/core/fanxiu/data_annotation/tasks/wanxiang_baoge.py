from __future__ import annotations

"""Acquire the Wanxiang refund box and preserve the purchased shop page."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.core.fanxiu.data_annotation.tasks.wanxiang_baoge_steps import (
    enter_wanxiang_shop,
    find_wanxiang_refund_offer,
    leave_wanxiang_shop,
    purchase_wanxiang_refund_offer,
    reveal_wanxiang_free_goods,
)
from backend.core.fanxiu.instrumentation.wanxiang_baoge import WanxiangRefreshBlocked


def run_wanxiang_baoge_flow(context: Any):
    """Review the box synchronously; recurrence belongs to the parent task."""
    yield from enter_wanxiang_shop(context)
    yield from reveal_wanxiang_free_goods(context)
    try:
        found = yield from find_wanxiang_refund_offer(context)
    except WanxiangRefreshBlocked as exc:
        yield from leave_wanxiang_shop(context)
        return {"ok": False, "outcome": "refresh_blocked", "reason": str(exc)}
    result = yield from purchase_wanxiang_refund_offer(context)
    yield from leave_wanxiang_shop(context)
    return {**result, "refreshes": found["refreshes"]}


def wanxiang_opens_on_date(
    period: dict[str, Any], target_date: str, timezone_name: str = "Asia/Shanghai"
) -> bool:
    """Only the server opening date authorizes the parent's synchronous review."""
    if period.get("complete") is not True:
        raise ValueError("万象宝阁开启时间不完整")
    start = int(period.get("start_time_ms") or 0)
    if start <= 0:
        raise ValueError("万象宝阁缺少有效开启时间")
    return (
        datetime.fromtimestamp(start / 1000, ZoneInfo(timezone_name)).date().isoformat()
        == target_date
    )
