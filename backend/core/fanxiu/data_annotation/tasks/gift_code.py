from __future__ import annotations

import threading
from typing import Any


class GiftCodeTaskMixin:
    def _execute_gift_code_task(self, ctx: dict[str, Any], codes: list[str], stop_event: threading.Event) -> None:
        with self._lock:
            self._set_status_locked("running", "对齐 #49 设置页", phase="align_settings")
        self._align_settings(ctx, stop_event)
        for index, code in enumerate(codes):
            self._raise_if_stopped(stop_event)
            with self._lock:
                self._set_status_locked(
                    "running",
                    f"处理第 {index + 1}/{len(codes)} 个：{code}",
                    current_index=index,
                    current_code=code,
                    phase="process_code",
                )
                self._log_locked("action", f"开始兑换：{code}")
            self._process_code(ctx, code, index == len(codes) - 1, stop_event)
        with self._lock:
            self._set_status_locked("running", "从 #49 回退", phase="finish_back")
        self._finish_from_settings(ctx, stop_event)
