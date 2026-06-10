from types import SimpleNamespace
from pathlib import Path

import backend.core.fanxiu_mumu_control as mumu


class _Frame:
    def __init__(self, width: int = 900, height: int = 1600):
        self.shape = (height, width, 3)


class _FakeCapture:
    def __init__(self, hwnd, *_args, **_kwargs):
        self.hwnd = hwnd

    def capture(self):
        return _Frame()


def _patch_window_fallback(monkeypatch):
    monkeypatch.setattr(mumu, "ensure_windows_runtime", lambda: None)
    monkeypatch.setattr(mumu, "set_dpi_awareness", lambda: None)
    monkeypatch.setattr(mumu, "find_window", lambda *_args, **_kwargs: SimpleNamespace(hwnd=123, title="MuMu Player"))
    monkeypatch.setattr(mumu, "WindowCapture", _FakeCapture)
    monkeypatch.setattr(mumu, "process_frame", lambda *_args, **_kwargs: _Frame())


def test_click_processed_point_falls_back_to_window_when_adb_is_unavailable(monkeypatch):
    clicked = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_tap_mumu_adb", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ADB 端口不可用")))
    monkeypatch.setattr(mumu, "map_processed_point_to_raw_point", lambda *_args, **_kwargs: (11, 22))
    monkeypatch.setattr(mumu, "click_window_raw_point", lambda hwnd, area, point: clicked.append((hwnd, area, point)))

    result = mumu.click_mumu_window_processed_point(
        x=10,
        y=20,
        title="MuMu",
        input_backend="adb",
        fixed_width=900,
        fixed_height=1600,
    )

    assert clicked == [(123, "outer", (11, 22))]
    assert result["input"] == "window"
    assert "ADB 端口不可用" in result["adb_input_error"]


def test_drag_processed_points_falls_back_to_window_when_adb_is_unavailable(monkeypatch):
    dragged = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_swipe_mumu_adb", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ADB 端口不可用")))
    monkeypatch.setattr(mumu, "map_processed_point_to_raw_point", lambda point, **_kwargs: (point[0] + 1, point[1] + 2))
    monkeypatch.setattr(
        mumu,
        "drag_window_raw_points",
        lambda hwnd, area, start, end, duration_ms=300: dragged.append((hwnd, area, start, end, duration_ms)),
    )

    result = mumu.drag_mumu_window_processed_points(
        start_x=10,
        start_y=20,
        end_x=30,
        end_y=40,
        duration_ms=500,
        title="MuMu",
        input_backend="adb",
        fixed_width=900,
        fixed_height=1600,
    )

    assert dragged == [(123, "outer", (11, 22), (31, 42), 500)]
    assert result["input"] == "window"
    assert "ADB 端口不可用" in result["adb_input_error"]


def test_mumu_adb_port_check_recovers_local_port(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    monkeypatch.setattr(mumu.fanxiu_android_proxy_service, "adb_path", lambda: Path("D:/adb.exe"))

    calls = []
    attempts = {"count": 0}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_create_connection(address, timeout=0):
        attempts["count"] += 1
        if attempts["count"] <= len(mumu.MUMU_ADB_PORTS):
            raise OSError("refused")
        return FakeSocket()

    def fake_run(command, **_kwargs):
        calls.append(tuple(str(item) for item in command))
        return mumu.subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(mumu.socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(mumu.subprocess, "run", fake_run)

    mumu._ensure_mumu_adb_port_available()

    assert (str(Path("D:/adb.exe")), "kill-server") in calls
    assert (str(Path("D:/adb.exe")), "start-server") in calls
    assert (str(Path("D:/adb.exe")), "connect", "127.0.0.1:7555") in calls


def test_mumu_adb_recovery_does_not_use_proxy_device_by_default(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    monkeypatch.setattr(mumu.fanxiu_android_proxy_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(tuple(str(item) for item in command))
        return mumu.subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(mumu.subprocess, "run", fake_run)
    monkeypatch.setattr(mumu.socket, "create_connection", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")))

    try:
        mumu._ensure_mumu_adb_port_available()
    except RuntimeError as exc:
        assert "ADB 端口不可用" in str(exc)
    else:
        raise AssertionError("expected adb unavailable")

    assert (str(Path("D:/adb.exe")), "connect", "192.168.31.181:5555") not in calls
