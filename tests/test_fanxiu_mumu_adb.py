from __future__ import annotations

import pytest
import time
import io
import subprocess

from PIL import Image

from backend.core.fanxiu.client import mumu_control as rotate


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
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "devices", lambda: [])
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
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "devices", lambda: [])

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
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])

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
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])

    rotate._ensure_mumu_adb_port_available()

    assert attempts == [
        *(("127.0.0.1", port) for port in rotate.MUMU_ADB_PORTS),
        ("192.168.31.181", 5555),
    ]


def test_mumu_adb_serial_candidates_env_has_priority(monkeypatch):
    monkeypatch.setenv("FANXIU_MUMU_ADB_SERIAL", "10.0.0.8:5555")
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])

    candidates = rotate._mumu_adb_serial_candidates()

    assert candidates == ["10.0.0.8:5555"]


def test_mumu_adb_input_reconnects_and_retries_after_input_failure(monkeypatch):
    calls: list[list[str]] = []

    def fake_run_quiet(args, **_kwargs):
        args = [str(item) for item in args]
        calls.append(args)
        if args[1] == "connect":
            return subprocess.CompletedProcess(args, 0, stdout="connected", stderr="")
        if args[1] == "disconnect":
            return subprocess.CompletedProcess(args, 0, stdout="disconnected", stderr="")
        if args[-1] == "wm size":
            return subprocess.CompletedProcess(args, 0, stdout="Physical size: 900x1600", stderr="")
        if args[-1] == "input tap 1 2":
            input_attempts = sum(1 for call in calls if call[-1] == "input tap 1 2")
            if input_attempts == 1:
                return subprocess.CompletedProcess(args, 255, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(rotate, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(rotate, "_mumu_adb_serial_candidates", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "adb_path", lambda: "adb")
    monkeypatch.setattr(rotate, "run_quiet_captured", fake_run_quiet)
    monkeypatch.setattr(rotate, "_append_mumu_device_health_event", lambda *_a, **_kw: None)
    monkeypatch.setattr(rotate.time, "sleep", lambda _seconds: None)

    result = rotate._run_mumu_adb_input("input tap 1 2")

    assert result["adb_serial"] == "192.168.31.181:5555"
    assert [call[1] for call in calls] == ["connect", "-s", "-s", "disconnect", "connect", "-s", "-s"]


def test_screencap_success_clears_cached_adb_failure(monkeypatch):
    rotate._mumu_adb_failure_cache = (time.monotonic() - 1, "ADB 端口不可用：127.0.0.1:7555")

    png = _png_bytes((120, 90, 60))
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


@pytest.mark.parametrize("cut", [1, 12, 30])
def test_screencap_rejects_truncated_png(cut):
    with pytest.raises(RuntimeError, match="PNG 不完整或无法解码"):
        rotate._validated_screencap_png(_png_bytes((120, 90, 60))[:-cut])


def test_screencap_truncated_session_uses_one_validated_fallback(monkeypatch):
    png = _png_bytes((120, 90, 60))
    monkeypatch.setattr(rotate, "_mumu_adb_session_shell_bytes", lambda *_a, **_kw: (png[:-30], {}))
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "adb_path", lambda: "adb")
    monkeypatch.setattr(rotate, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:5555"])
    calls = []

    def capture(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=png, stderr=b"")

    monkeypatch.setattr(rotate, "run_quiet", capture)
    data, meta = rotate.screencap_mumu_adb_png()
    assert data == png
    assert len(calls) == 1
    assert calls[0][-3:] == ["exec-out", "screencap", "-p"]
    assert "PNG 不完整" in meta["capture_fallback_reason"]


def test_screencap_invalid_fallback_fails_without_clearing_health(monkeypatch):
    png = _png_bytes((120, 90, 60))[:-30]
    monkeypatch.setattr(rotate, "_mumu_adb_session_shell_bytes", lambda *_a, **_kw: (png, {}))
    monkeypatch.setattr(rotate.fanxiu_adb_device_service, "adb_path", lambda: "adb")
    monkeypatch.setattr(rotate, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:5555"])
    monkeypatch.setattr(rotate, "run_quiet", lambda args, **_kw: subprocess.CompletedProcess(args, 0, stdout=png, stderr=b""))
    rotate._mumu_adb_failure_cache = (time.monotonic() + 60, "ADB 端口不可用")
    with pytest.raises(RuntimeError, match="PNG 不完整"):
        rotate.screencap_mumu_adb_png()
    assert rotate._get_mumu_adb_failure_cache() == "ADB 端口不可用"


def test_mumu_adb_black_frame_summary_detects_uniform_black():
    summary = rotate._mumu_adb_png_black_frame_summary(_png_bytes((0, 0, 0)))

    assert summary["black"] is True
    assert summary["near_dark_ratio"] == 1.0


def test_mumu_adb_black_frame_summary_keeps_normal_frame():
    summary = rotate._mumu_adb_png_black_frame_summary(_png_bytes((120, 90, 60)))

    assert summary["black"] is False


def test_match_frame_retention_prunes_old_numbered_images(monkeypatch, tmp_path):
    output_dir = tmp_path / "match"
    output_dir.mkdir()
    for index in range(1, 5):
        (output_dir / f"{index:04d}.png").write_bytes(f"old-{index}".encode("ascii"))

    monkeypatch.setenv("FX_MATCH_FRAME_DIR", str(output_dir))
    monkeypatch.setenv(rotate.MATCH_FRAME_MAX_FILES_ENV, "3")

    index, output = rotate._save_limited_match_frame(b"new", ".png")

    assert index == 5
    assert output.name == "0005.png"
    assert sorted(path.name for path in output_dir.iterdir()) == ["0003.png", "0004.png", "0005.png"]


def test_black_frame_failure_recovers_only_after_observation_window(monkeypatch):
    recovered: list[dict[str, object]] = []

    rotate.reset_mumu_device_health_state()
    monkeypatch.setattr(rotate, "_read_mumu_device_recovery_state", lambda: {})
    monkeypatch.setattr(rotate, "_mumu_device_last_recovery_at", lambda: 0.0)
    monkeypatch.setattr(rotate, "mumu_device_health_check", lambda **_kwargs: {"status": "healthy"})
    monkeypatch.setattr(rotate, "_mumu_device_in_startup_grace", lambda: (False, 0.0))
    monkeypatch.setattr(rotate, "_append_mumu_device_health_event", lambda *_a, **_kw: None)
    monkeypatch.setattr(rotate, "_mumu_frame_unusable_recovery_seconds", lambda: 30.0)
    clock = [1000.0]
    monkeypatch.setattr(rotate.time, "time", lambda: clock[0])

    def fake_recover_mumu_device(**kwargs):
        recovered.append(dict(kwargs))
        return {"status": "healthy", "recovered": True}

    monkeypatch.setattr(rotate, "recover_mumu_device", fake_recover_mumu_device)

    error = "MuMu ADB截图疑似黑屏，需重建模拟器画面链路"
    state = rotate.record_mumu_adb_failure(error, recover=True)
    assert state["recovery_deferred"] == "frame_unusable_observation_window"
    assert recovered == []
    clock[0] += 10.0
    state = rotate.record_mumu_adb_failure(error, recover=True)
    assert state["recovered"] is False
    assert recovered == []
    clock[0] += 20.0
    state = rotate.record_mumu_adb_failure(error, recover=True)

    assert state["recovered"] is True
    assert recovered == [{"vmindex": "1", "reason": "adb_failure:MuMu ADB截图疑似黑屏，需重建模拟器画面链路", "force_restart": True}]

