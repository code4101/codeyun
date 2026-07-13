from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


class MozuTaskMixin:
    def daily_mozu_flow(self, runtime: Any):
        now = datetime.now()
        window_start = now.replace(hour=12, minute=30, second=0, microsecond=0)
        window_end = now.replace(hour=12, minute=35, second=0, microsecond=0)
        next_run = window_start if now < window_start else window_start + timedelta(days=1)
        next_run_text = next_run.strftime("%Y-%m-%d %H:%M:%S")
        if not window_start <= now <= window_end:
            return {
                "result": "success",
                "message": "日常_魔祖：当前不在 12:30:00-12:35:00 窗口，未执行游戏操作",
                "next_time": next_run_text,
                "current_scene": None,
            }

        yield from runtime.go_scene(34)
        yield from runtime.wait_click_then_view(34, "日程", 66)
        yield from runtime.wait_click_then_view(66, "前往", 336)
        yield from runtime.wait_click_then_view(336, "前往", 337)
        yield from runtime.wait_click_then_view(337, "前往", 339)
        yield from runtime.wait_click_then_view(339, "返回", 34)
        return {
            "result": "success",
            "message": "日常_魔祖：已完成并回到世界 #34",
            "next_time": next_run_text,
            "current_scene": 34,
        }
