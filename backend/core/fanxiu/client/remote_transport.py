"""Scope the single Kernel's device I/O to one authenticated client worker.

The binding is visible process-wide so local fallback is rejected on every thread.
Device I/O belongs to the Cell thread; remote Runtime reads must finish in-Cell.
Remote failures never select a local transport as a fallback.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import threading
from typing import Any

_binding: Any = None
_ownership = threading.Lock()
_owner_thread: int | None = None
_runtime_ready = False


def remote_device_active() -> bool:
    return _binding is not None


def remote_device_scope_id() -> str:
    """Return the bound worker identity for device-specific caches; local is empty."""
    transport = _binding
    return str(transport.worker_id) if transport is not None else ""


def reject_local_device_access(operation: str) -> None:
    if remote_device_active():
        raise RuntimeError(f"客户端模式禁止回落服务端设备：{operation}")


@contextmanager
def use_remote_device(worker_id: str):
    """Bind a registered client for one complete formal Task attempt."""
    global _binding, _owner_thread, _runtime_ready
    if not _ownership.acquire(blocking=False):
        raise RuntimeError("已有客户端占用唯一 Kernel 的设备通道")
    try:
        from backend.core.fanxiu.remote.device_bridge import get_kernel_transport
        _binding = get_kernel_transport(worker_id)
        _owner_thread = threading.get_ident()
        _runtime_ready = False
        health = call_remote_device("health", {}, timeout_s=20)
        if health.get("status") != "healthy":
            raise RuntimeError("客户端设备尚未就绪")
        yield
    finally:
        _binding = None
        _owner_thread = None
        _runtime_ready = False
        _ownership.release()


def call_remote_device(operation: str, args: dict | None = None, *, timeout_s: float = 15) -> dict:
    transport = _binding
    if transport is None:
        raise RuntimeError("尚未绑定客户端设备")
    if threading.get_ident() != _owner_thread:
        raise RuntimeError("客户端设备读取必须在所属 Cell 内完成，禁止后台线程跨作业访问")
    result = transport.call(operation, dict(args or {}), timeout_s=timeout_s)
    if not isinstance(result, dict):
        raise RuntimeError("客户端设备回执格式错误")
    return result


def ensure_remote_runtime_ready() -> None:
    """Request elevated Android reads only when a Task first asks for Runtime."""
    global _runtime_ready
    if not remote_device_active() or _runtime_ready:
        return
    result = call_remote_device("root", timeout_s=35)
    if result.get("rooted") is not True:
        raise RuntimeError("客户端未获得 Runtime 内存读取权限")
    _runtime_ready = True


def remote_metadata(result: dict) -> dict:
    metadata = dict(result.get('metadata') or {})
    size = metadata.get('adb_size')
    if isinstance(size, (list, tuple)) and len(size) == 2:
        metadata['adb_size'] = f"Physical size: {int(size[0])}x{int(size[1])}"
    metadata['input'] = 'remote-client'
    return metadata


def remote_bytes(result: dict, *, max_bytes: int = 32 * 1024 * 1024) -> tuple[bytes, dict]:
    encoded = result.get('data_base64')
    if not isinstance(encoded, str) or len(encoded) > (max_bytes + 2) // 3 * 4:
        raise RuntimeError("客户端二进制回执缺失或超限")
    data = base64.b64decode(encoded, validate=True)
    if len(data) > max_bytes:
        raise RuntimeError("客户端二进制回执超限")
    return data, remote_metadata(result)
