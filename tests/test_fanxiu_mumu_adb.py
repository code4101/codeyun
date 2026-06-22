from __future__ import annotations

import pytest
import time
import io

from PIL import Image

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


@pytest.fixture(autouse=True)
def _isolate_mumu_adb_candidates(monkeypatch):
    for key in rotate.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(rotate.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    monkeypatch.setenv(rotate.MUMU_ADB_PORT_PROBE_TIMEOUT_ENV, "0.15")
    monkeypatch.setattr(rotate, "_mumu_manager_adb_serial_candidates", lambda: [])


def test_mumu_adb_port_probe_caches_all_ports_unavailable(monkeypatch):
    attempts: list[tuple[tuple[str, int], float]] = []

    def fail_connect(address, timeout):
        attempts.append((address, timeout))
        raise OSError("refused")

    monkeypatch.setattr(rotate.socket, "create_connection", fail_connect)
    monkeypatch.setattr(rotate.fanxiu_android_proxy_service, "devices", lambda: [])
    monkeypatch.setattr(rotate, "_recover_mumu_adb_ports", lambda: False)

    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        rotate._ensure_mumu_adb_port_available()

    assert attempts == [(("127.0.0.1", port), 0.15) for port in rotate.MUMU_ADB_PORTS]

    attempts.clear()
    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        rotate._ensure_mumu_adb_port_available()

    assert attempts == [(("127.0.0.1", port), 0.15) for port in rotate.MUMU_ADB_PORTS]


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


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (90, 160), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_mumu_adb_black_frame_summary_detects_uniform_black():
    summary = rotate._mumu_adb_png_black_frame_summary(_png_bytes((0, 0, 0)))

    assert summary["black"] is True
    assert summary["near_dark_ratio"] == 1.0


def test_mumu_adb_black_frame_summary_keeps_normal_frame():
    summary = rotate._mumu_adb_png_black_frame_summary(_png_bytes((120, 90, 60)))

    assert summary["black"] is False


def test_black_frame_failure_triggers_recovery(monkeypatch):
    recovered: list[dict[str, object]] = []

    monkeypatch.setattr(rotate, "mumu_device_health_check", lambda **_kwargs: {"status": "healthy"})

    def fake_recover_mumu_device(**kwargs):
        recovered.append(dict(kwargs))
        return {"status": "healthy", "recovered": True}

    monkeypatch.setattr(rotate, "recover_mumu_device", fake_recover_mumu_device)

    state = rotate.record_mumu_adb_failure("MuMu ADB截图疑似黑屏，需重建模拟器画面链路", recover=True)

    assert state["recovered"] is True
    assert recovered == [{"vmindex": "1", "reason": "adb_failure:MuMu ADB截图疑似黑屏，需重建模拟器画面链路", "force_restart": True}]

