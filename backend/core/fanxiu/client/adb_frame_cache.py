"""Windows 跨进程最新 ADB 帧：Kernel、后端和信息窗共享约 8 MiB 内存。

缓存只复制已获得的 PNG，不采集、不编码、不落盘。命名互斥量保护帧身份
与字节的一致性；业务采集发布时不等待读者，争用时跳过缓存而不拖慢任务。
所有持有者退出后，Windows 自动回收映射。
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
import struct
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

SIZE = 8 * 1024 * 1024
HEADER = struct.Struct('<8sdII')
MAGIC = b'CYADB001'
_init_lock = threading.Lock()
_mapping: mmap.mmap | None = None
_mutex: Any = None
_capture_mutex: Any = None


def _initialize() -> bool:
    global _mapping, _mutex, _capture_mutex
    if os.name != 'nt':
        return False
    if _mapping is None:
        with _init_lock:
            if _mapping is None:
                import win32event
                from backend.core.settings import get_settings
                identity = hashlib.sha256(str(get_settings().data_dir.resolve()).casefold().encode()).hexdigest()[:16]
                name = f'Local\\CodeYun.AdbFrame.{identity}'
                _mutex = win32event.CreateMutex(None, False, name + '.Lock')
                _capture_mutex = win32event.CreateMutex(None, False, name + '.Capture')
                _mapping = mmap.mmap(-1, SIZE, tagname=name)
    return True


def publish_adb_frame(data: bytes, metadata: dict[str, Any], *, captured_at: float | None = None) -> bool:
    """Best-effort cache publication; failure never changes capture success."""
    try:
        if not _initialize():
            return False
        import win32event
        encoded = json.dumps(metadata, ensure_ascii=True).encode()
        if len(data) + len(encoded) + HEADER.size > SIZE:
            return False
        acquired = win32event.WaitForSingleObject(_mutex, 0) in (0, 0x80)
        if not acquired:
            return False
        try:
            assert _mapping is not None
            _mapping[:HEADER.size] = HEADER.pack(MAGIC, captured_at or time.time(), len(encoded), len(data))
            _mapping[HEADER.size:HEADER.size + len(encoded)] = encoded
            _mapping[HEADER.size + len(encoded):HEADER.size + len(encoded) + len(data)] = data
            return True
        finally:
            win32event.ReleaseMutex(_mutex)
    except Exception:
        return False


def read_adb_frame(*, max_age_seconds: float | None = None) -> tuple[bytes, dict[str, Any], float] | None:
    """Read one coherent frame; None means missing, stale or briefly busy."""
    try:
        if not _initialize():
            return None
        import win32event
        if win32event.WaitForSingleObject(_mutex, 0) not in (0, 0x80):
            return None
        try:
            assert _mapping is not None
            magic, captured_at, meta_size, data_size = HEADER.unpack(_mapping[:HEADER.size])
            if magic != MAGIC or captured_at <= 0 or captured_at > time.time() + 1:
                return None
            if max_age_seconds is not None and time.time() - captured_at > max_age_seconds:
                return None
            if meta_size > 65536 or data_size < 1 or HEADER.size + meta_size + data_size > SIZE:
                return None
            start = HEADER.size + meta_size
            metadata = json.loads(_mapping[HEADER.size:start])
            return _mapping[start:start + data_size], metadata, captured_at
        finally:
            win32event.ReleaseMutex(_mutex)
    except Exception:
        return None


@contextmanager
def claim_adb_frame_refresh(*, timeout_ms: int = 0) -> Iterator[bool]:
    """Combine stale-frame refreshes across cooperating processes."""
    import win32event
    acquired = _initialize() and win32event.WaitForSingleObject(_capture_mutex, timeout_ms) in (0, 0x80)
    try:
        yield bool(acquired)
    finally:
        if acquired:
            win32event.ReleaseMutex(_capture_mutex)
