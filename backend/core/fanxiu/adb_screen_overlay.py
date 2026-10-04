"""信息窗的 ADB 帧来源：共享缓存优先，过期一秒按需补帧。

Tk 线程只控制 visible 和读取 snapshot；采集与输入在工作线程串行执行。
隐藏后不发起新截图，失败指数退避，始终保留最后成功画面。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from backend.core.fanxiu.client.adb_frame_cache import claim_adb_frame_refresh, read_adb_frame


class AdbScreenSource:
    def __init__(self) -> None:
        self.visible = False
        self.snapshot: tuple[bytes, dict[str, Any], float] | None = None
        self.error = ''
        self.capture_seconds = 0.0
        self.input_busy = False
        self.last_input: dict[str, Any] = {}
        self._input: dict[str, Any] | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name='fanxiu-adb-overlay', daemon=True)
        self._thread.start()

    def send_gesture(self, payload: dict[str, Any]) -> bool:
        """One in-flight gesture, no accumulated mouse-movement queue."""
        if not self.visible or self.input_busy:
            return False
        self.input_busy = True
        self._input = dict(payload)
        self._wake.set()
        return True

    def close(self) -> None:
        self.visible = False
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        retry_at = 0.0
        failures = 0
        while not self._stop.is_set():
            try:
                gesture = self._input
                if gesture is not None:
                    self._input = None
                    try:
                        if self.visible:
                            from backend.core.fanxiu.game.window_actions import click_game_window2_service, drag_game_window2_service
                            action = drag_game_window2_service if 'start_x' in gesture else click_game_window2_service
                            result = action(gesture)
                            self.last_input = {'ok': True, 'at': time.time(), 'gesture': gesture, 'result': result}
                    except Exception as exc:
                        self.last_input = {'ok': False, 'at': time.time(), 'gesture': gesture, 'error': str(exc)}
                        raise
                    finally:
                        self.input_busy = False
                if self.visible:
                    cached = read_adb_frame()
                    if cached is not None:
                        self.snapshot = cached
                    if (cached is None or time.time() - cached[2] >= 1.0) and time.monotonic() >= retry_at:
                        with claim_adb_frame_refresh() as acquired:
                            # Another observer may have refreshed while we waited.
                            fresh = read_adb_frame(max_age_seconds=1.0)
                            if fresh is not None:
                                self.snapshot = fresh
                            elif acquired and self.visible and not self._stop.is_set():
                                from backend.core.fanxiu.client.mumu_control import screencap_mumu_adb_cached_png
                                started = time.monotonic()
                                data, metadata = screencap_mumu_adb_cached_png(max_age_seconds=1.0)
                                self.capture_seconds = time.monotonic() - started
                                self.snapshot = read_adb_frame() or (data, metadata, time.time())
                                self.error = ''
                                failures = 0
            except Exception as exc:
                self.error = f'{type(exc).__name__}: {exc}'
                failures = min(failures + 1, 5)
                retry_at = time.monotonic() + min(30, 2 ** failures)
            self._wake.wait(0.2 if self.visible else 1.0)
            self._wake.clear()
