from __future__ import annotations

import pytest
import time

from backend.core import fanxiu_sunlogin_rotate as rotate


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def setup_function() -> None:
    rotate._clear_mumu_adb_failure_cache()


def teardown_function() -> None:
    rotate._clear_mumu_adb_failure_cache()


def test_mumu_adb_port_probe_caches_all_ports_unavailable(monkeypatch):
    attempts: list[tuple[tuple[str, int], float]] = []

    def fail_connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("refused")

    monkeypatch.setattr(rotate.socket, "create_connection", fail_connect)

    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        rotate._ensure_mumu_adb_port_available()

    assert attempts == [(("127.0.0.1", port), 0.05) for port in rotate.MUMU_ADB_PORTS]

    attempts.clear()
    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        rotate._ensure_mumu_adb_port_available()

    assert attempts == []


def test_mumu_adb_port_probe_returns_when_any_port_is_open(monkeypatch):
    attempts: list[int] = []

    def connect(address, timeout):
        attempts.append(address[1])
        if address[1] != rotate.MUMU_ADB_PORTS[1]:
            raise OSError("refused")
        return _FakeSocket()

    monkeypatch.setattr(rotate.socket, "create_connection", connect)

    rotate._ensure_mumu_adb_port_available()

    assert attempts == [rotate.MUMU_ADB_PORTS[0], rotate.MUMU_ADB_PORTS[1]]


def test_screencap_success_clears_cached_adb_failure(monkeypatch):
    rotate._mumu_adb_failure_cache = (time.monotonic() - 1, "ADB 端口不可用：127.0.0.1:7555")

    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    monkeypatch.setattr(rotate, "_mumu_adb_session_shell_bytes", lambda *_args, **_kwargs: (png, {"input": "test"}))

    data, meta = rotate.screencap_mumu_adb_png()

    assert data == png
    assert meta == {"input": "test"}
    assert rotate._get_mumu_adb_failure_cache() is None
