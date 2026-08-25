from __future__ import annotations

import ctypes
import os
from datetime import datetime, timezone
from pathlib import Path


def parse_publication_timestamp(value: str) -> float:
    """Parse an ISO publication time without silently assuming local time."""

    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("发布日期为空。")
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def set_file_publication_time(path: str | Path, published_at: str) -> float:
    """Align filesystem creation/write times to an authoritative publication time.

    Windows exposes a writable creation timestamp, so both creation time and last
    write time are updated. Other systems can only portably update mtime. File
    contents and embedded image metadata are intentionally left untouched.
    """

    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    timestamp = parse_publication_timestamp(published_at)
    if os.name == "nt":
        _set_windows_file_times(target, timestamp)
    else:
        current = target.stat()
        os.utime(target, (current.st_atime, timestamp))
    return timestamp


def _set_windows_file_times(path: Path, timestamp: float) -> None:
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    set_file_time = kernel32.SetFileTime
    set_file_time.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    set_file_time.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    raw_value = int((timestamp + 11_644_473_600) * 10_000_000)
    file_time = FileTime(raw_value & 0xFFFFFFFF, raw_value >> 32)
    handle = create_file(
        str(path),
        0x0100,  # FILE_WRITE_ATTRIBUTES
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not set_file_time(handle, ctypes.byref(file_time), None, ctypes.byref(file_time)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close_handle(handle)
