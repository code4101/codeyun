from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class ZhenxieTaskMixin:
    def daily_zhenxie_flow(self, runtime: Any):
        now = datetime.now()
        window_start = now.replace(hour=21, minute=0, second=0, microsecond=0)
        window_end = now.replace(hour=21, minute=5, second=0, microsecond=0)
        next_run = window_start if now < window_start else window_start + timedelta(days=1)
        next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
        if not window_start <= now <= window_end:
            return {
                "result": "success",
                "message": "\u65e5\u5e38_\u9547\u90aa\uff1a\u5f53\u524d\u4e0d\u5728 21:00:00-21:05:00 \u7a97\u53e3\uff0c\u672a\u6267\u884c\u6e38\u620f\u64cd\u4f5c",
                "next_time": next_run_text,
                "current_scene": None,
            }

        yield from runtime.goto_view(272, layer0_wait_seconds=90.0)
        yield from runtime.wait_click(272, "\u524d\u5f80")
        yield from runtime.wait_action_settle(30.0)
        return {
            "result": "success",
            "message": "\u65e5\u5e38_\u9547\u90aa\uff1a\u5df2\u70b9\u51fb #272\u300c\u524d\u5f80\u300d\u5e76\u7b49\u5f85 30 \u79d2\uff0c\u672c\u6b21\u5b8c\u6210",
            "next_time": next_run_text,
            "current_scene": 272,
        }
