from __future__ import annotations

import pytest
import time

from backend.core.fanxiu.runtime import mumu_control as rotate


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


def setup_function() -> None:
    rotate._clear_mumu_adb_failure_cache()
    rotate._MUMU_ADB_SESSION.clear()


def teardown_function() -> None:
    rotate._clear_mumu_adb_failure_cache()
    rotate._MUMU_ADB_SESSION.clear()


def test_mumu_adb_port_probe_caches_all_ports_unavailable(monkeypatch):
    attempts: list[tuple[tuple[str, int], float]] = []

    def fail_connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("refused")

    monkeypatch.setattr(rotate.socket, "create_connection", fail_connect)
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: [])

    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        rotate._ensure_mumu_adb_port_available()

    assert attempts == [(("127.0.0.1", port), 0.15) for port in rotate.MUMU_ADB_PORTS]

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
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: [])

    rotate._ensure_mumu_adb_port_available()

    assert attempts == [rotate.MUMU_ADB_PORTS[0], rotate.MUMU_ADB_PORTS[1]]


def test_mumu_adb_port_probe_uses_local_ports_before_proxy_devices(monkeypatch):
    attempts: list[tuple[str, int]] = []

    def connect(address, timeout):
        del timeout
        attempts.append(address)
        if address != ("127.0.0.1", rotate.MUMU_ADB_PORTS[0]):
            raise OSError("refused")
        return _FakeSocket()

    monkeypatch.setattr(rotate.socket, "create_connection", connect)
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])

    rotate._ensure_mumu_adb_port_available()

    assert attempts == [("127.0.0.1", rotate.MUMU_ADB_PORTS[0])]


def test_mumu_adb_port_probe_can_opt_into_proxy_devices(monkeypatch):
    attempts: list[tuple[str, int]] = []

    def connect(address, timeout):
        del timeout
        attempts.append(address)
        if address != ("192.168.31.181", 5555):
            raise OSError("refused")
        return _FakeSocket()

    monkeypatch.setenv(rotate.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, "1")
    monkeypatch.setattr(rotate.socket, "create_connection", connect)
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])

    rotate._ensure_mumu_adb_port_available()

    assert attempts == [
        *(("127.0.0.1", port) for port in rotate.MUMU_ADB_PORTS),
        ("192.168.31.181", 5555),
    ]


def test_mumu_adb_serial_candidates_env_has_priority(monkeypatch):
    monkeypatch.setenv("FANXIU_MUMU_ADB_SERIAL", "10.0.0.8:5555")
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])

    candidates = rotate._mumu_adb_serial_candidates()

    assert candidates == ["10.0.0.8:5555"]


def test_screencap_success_clears_cached_adb_failure(monkeypatch):
    rotate._mumu_adb_failure_cache = (time.monotonic() - 1, "ADB 端口不可用：127.0.0.1:7555")

    png = b"\x89PNG\r\n\x1a\n" + b"payload"
    monkeypatch.setattr(rotate, "_mumu_adb_session_shell_bytes", lambda *_args, **_kwargs: (png, {"input": "test"}))

    data, meta = rotate.screencap_mumu_adb_png()

    assert data == png
    assert meta == {"input": "test"}
    assert rotate._get_mumu_adb_failure_cache() is None

