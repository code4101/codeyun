from types import SimpleNamespace
from pathlib import Path
import sys
import threading

import pytest

import backend.core.fanxiu.game.window_actions as window_actions
import backend.core.fanxiu.client.mumu_control as mumu

_REAL_SCHEDULE_LOGIN_AFTER_RESTART = mumu._schedule_login_job_after_mumu_restart
_REAL_ENSURE_MUMU_ADB_ROOT = mumu.ensure_mumu_adb_root
_REAL_CONFIGURE_MUMU_DISPLAY = mumu.configure_mumu_display


@pytest.mark.parametrize("initial_width", [900, 1280])
def test_native_display_configuration_is_verified_and_idempotent(monkeypatch, initial_width):
    persisted = {
        "resolution_mode": "custom", "resolution_width.custom": str(initial_width),
        "resolution_height.custom": "1600.000000", "resolution_dpi.custom": "320.000000",
    }
    writes = []

    def manager(args):
        if "--value" in args:
            writes.append(args)
            persisted["resolution_width.custom"] = "900.000000"
        return dict(persisted)

    monkeypatch.setattr(mumu, "_run_mumu_manager_json", manager)
    result = _REAL_CONFIGURE_MUMU_DISPLAY(width=900, height=1600, dpi=320)
    assert result["changed"] is (initial_width != 900)
    assert len(writes) == int(initial_width != 900)
    assert result["restarted"] is False
    assert float(result["settings"]["resolution_width.custom"]) == 900


def test_native_display_failed_persistence_does_not_restart_vm(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_run_mumu_manager_json", lambda args: {"resolution_mode": "default"})
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(RuntimeError, match="持久显示配置写入未生效"):
        _REAL_CONFIGURE_MUMU_DISPLAY(width=900, height=1600, dpi=320, restart=True)
    assert calls == []


def test_display_cold_start_waits_for_surface_before_desktop_geometry(monkeypatch):
    events = []
    persisted = {
        "resolution_mode": "custom", "resolution_width.custom": "900",
        "resolution_height.custom": "1600", "resolution_dpi.custom": "320",
    }
    monkeypatch.setattr(mumu, "_run_mumu_manager_json", lambda args: dict(persisted))
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda vmindex, command, **kwargs: events.append(command))
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", lambda vmindex: {
        "is_process_started": False, "is_android_started": True, "adb_host_ip": "127.0.0.1",
    })
    monkeypatch.setattr(mumu, "wait_mumu_adb_online", lambda **kwargs: {"adb": {"adb_serial": "127.0.0.1:5555"}})
    monkeypatch.setattr(mumu, "_run_mumu_adb_shell_text", lambda command, **kwargs: (
        "Physical size: 900x1600" if command == "wm size" else "Physical density: 320", {},
    ))
    monkeypatch.setattr(mumu, "wait_mumu_recovery_frame_ready", lambda **kwargs: events.append("surface") or {"ok": True})
    monkeypatch.setattr(mumu, "normalize_mumu_desktop_window_size", lambda **kwargs: events.append("xywh") or {"ok": True})
    result = _REAL_CONFIGURE_MUMU_DISPLAY(width=900, height=1600, dpi=320, restart=True)
    assert events == ["shutdown", "launch", "surface", "xywh"]
    assert result["window_size"]["ok"]


def _registered_prepared_probe(
    serial: str = "192.168.31.181:5555",
    adb_size: str = "Physical size: 900x1600",
) -> dict[str, object]:
    token, verified_at = mumu._issue_mumu_adb_prepared_probe(serial, adb_size)
    return {
        "input": "adb-cli",
        "adb_serial": serial,
        "adb_size": adb_size,
        "adb_prepared_probe_token": token,
        "adb_verified_at_monotonic": verified_at,
    }


@pytest.fixture(autouse=True)
def _patch_mumu_device_health_logs(monkeypatch, tmp_path):
    # Recovery lifecycle contracts must not change host emulator settings.
    monkeypatch.setattr(mumu, "configure_mumu_display", lambda **kwargs: {"changed": False, "restarted": False})
    # Recovery tests must never enumerate/terminate a real host VM. The
    # exact-index ownership test supplies its own synthetic process inventory.
    monkeypatch.setattr(mumu.psutil, "process_iter", lambda *_args, **_kwargs: iter(()))
    # Input now uses the captured launcher; retain each test's explicit command
    # double rather than falling through to a real adb executable.
    monkeypatch.setattr(mumu, "run_quiet_captured", lambda *args, **kwargs: mumu.run_quiet(*args, **kwargs))
    monkeypatch.setattr(mumu, "_mumu_device_health_log_dir", lambda: tmp_path)
    monkeypatch.setattr(mumu, "_mumu_manager_discovery_cache_path", lambda: tmp_path / "manager_discovery.json")
    monkeypatch.setattr(mumu, "_mumu_manager_discovery_lock_path", lambda: tmp_path / "manager_discovery.lock")
    monkeypatch.setattr(
        mumu,
        "_schedule_login_job_after_mumu_restart",
        lambda **_kwargs: {"ok": True, "task_id": "login-game", "next_time": "now"},
    )
    monkeypatch.setattr(
        mumu,
        "ensure_mumu_adb_root",
        lambda **_kwargs: {"ok": True, "identity": "uid=0(root)"},
    )
    monkeypatch.setattr(
        mumu.socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline in unit test")),
    )
    monkeypatch.setattr(mumu, "_collect_mumu_host_resource_snapshot", lambda: {"memory": {"available_mb": 123}})
    monkeypatch.setattr(
        mumu,
        "_collect_mumu_native_diagnostics",
        lambda: {"suspected_causes": ["probe"], "marker_lines": [{"file": "shell.log", "line": "VERR_TEST"}]},
    )
    with mumu._MUMU_MANAGER_ADB_SERIAL_CACHE_LOCK:
        mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.clear()
        mumu._MUMU_MANAGER_VM_INDEX_CACHE.clear()
    with mumu._MUMU_ADB_PREPARED_PROBE_LOCK:
        mumu._MUMU_ADB_PREPARED_PROBES.clear()
    yield
    with mumu._MUMU_MANAGER_ADB_SERIAL_CACHE_LOCK:
        mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.clear()
        mumu._MUMU_MANAGER_VM_INDEX_CACHE.clear()
    with mumu._MUMU_ADB_PREPARED_PROBE_LOCK:
        mumu._MUMU_ADB_PREPARED_PROBES.clear()


def test_successful_mumu_restart_only_makes_login_job_due(monkeypatch):
    from backend.core.fanxiu.data_annotation import kernel_scheduler_control

    calls = []
    monkeypatch.setattr(
        kernel_scheduler_control,
        "schedule_login_job_first",
        lambda **kwargs: calls.append(kwargs) or "2026-08-03 12:30:00",
    )

    result = _REAL_SCHEDULE_LOGIN_AFTER_RESTART()

    assert result == {
        "ok": True,
        "task_id": "login-game",
        "next_time": "2026-08-03 12:30:00",
    }
    assert len(calls) == 1


class _Frame:
    def __init__(self, width: int = 900, height: int = 1600):
        self.shape = (height, width, 3)


class _FakeCapture:
    def __init__(self, hwnd, *_args, **_kwargs):
        self.hwnd = hwnd

    def capture(self):
        return _Frame()


def test_adb_mjpeg_stream_skips_capture_errors_without_emitting_non_image_parts(monkeypatch):
    calls = {"count": 0}

    def fake_frame(*, min_interval):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary adb failure")
        return b"png-frame"

    monkeypatch.setattr(mumu, "_get_mumu_adb_stream_frame", fake_frame)
    monkeypatch.setattr(mumu.time, "sleep", lambda _seconds: None)

    part = next(mumu.stream_mumu_adb_screencap_mjpeg(fps=1.0))

    assert calls["count"] == 2
    assert b"Content-Type: image/png" in part
    assert b"Content-Length: 9\r\n\r\n" in part
    assert b"text/plain" not in part
    assert b"png-frame" in part


def test_adb_frame_sequence_advances_even_when_pixels_are_identical(monkeypatch):
    monkeypatch.setattr(mumu, "screencap_mumu_adb_png", lambda: (b"same-png", {}))
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_data", None)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_timestamp", 0.0)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_captured_at", 0.0)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_sequence", 0)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_consecutive_failures", 0)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_last_error", "")

    first, first_status = mumu.capture_fresh_mumu_adb_stream_frame()
    second, second_status = mumu.capture_fresh_mumu_adb_stream_frame()

    assert first == second == b"same-png"
    assert first_status["sequence"] == 1
    assert second_status["sequence"] == 2
    assert second_status["ready"] is True


def test_forced_fresh_frame_never_falls_back_to_cached_bytes(monkeypatch):
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_data", b"stale-png")
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_timestamp", mumu.time.monotonic())
    monkeypatch.setattr(
        mumu,
        "screencap_mumu_adb_png",
        lambda: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )

    with pytest.raises(RuntimeError, match="capture failed"):
        mumu.capture_fresh_mumu_adb_stream_frame()


def test_frame_status_remains_readable_while_capture_is_blocked(monkeypatch):
    capture_started = threading.Event()
    release_capture = threading.Event()
    captured = []

    def slow_capture():
        capture_started.set()
        assert release_capture.wait(2)
        return b"new-png", {}

    monkeypatch.setattr(mumu, "screencap_mumu_adb_png", slow_capture)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_data", None)
    monkeypatch.setattr(mumu, "_mumu_adb_stream_frame_timestamp", 0.0)
    capture_thread = threading.Thread(target=mumu.capture_fresh_mumu_adb_stream_frame)
    capture_thread.start()
    assert capture_started.wait(1)

    status_thread = threading.Thread(target=lambda: captured.append(mumu.get_mumu_adb_stream_frame_status()))
    status_thread.start()
    status_thread.join(0.5)
    try:
        assert not status_thread.is_alive()
        assert captured[0]["ready"] is False
    finally:
        release_capture.set()
        capture_thread.join(2)


def _patch_window_fallback(monkeypatch):
    monkeypatch.setattr(mumu, "ensure_windows_runtime", lambda: None)
    monkeypatch.setattr(mumu, "set_dpi_awareness", lambda: None)
    monkeypatch.setattr(
        mumu,
        "_find_mumu_desktop_main_window",
        lambda: {
            "hwnd": 123,
            "title": "凡人修仙传：人界篇-Powered by MuMu模拟器",
            "class": "Qt5156QWindowIcon",
            "extended_rect_physical": [0, 0, 902, 1630],
        },
    )
    monkeypatch.setattr(mumu, "find_window", lambda *_args, **_kwargs: SimpleNamespace(hwnd=123, title="MuMu Player"))
    monkeypatch.setattr(mumu, "WindowCapture", _FakeCapture)
    monkeypatch.setattr(mumu, "process_frame", lambda *_args, **_kwargs: _Frame())


def test_capture_mumu_window_frame_defaults_to_annotation_canvas(monkeypatch):
    kwargs_list = []
    _patch_window_fallback(monkeypatch)

    def fake_process_frame(*_args, **kwargs):
        kwargs_list.append(kwargs)
        return _Frame(width=kwargs["fixed_width"], height=kwargs["fixed_height"])

    monkeypatch.setattr(mumu, "process_frame", fake_process_frame)

    frame = mumu.capture_mumu_window_frame()

    assert frame.shape == (1600, 900, 3)
    assert kwargs_list[-1]["fixed_width"] == 900
    assert kwargs_list[-1]["fixed_height"] == 1600
    assert mumu.DEFAULT_CROP == "0,60,4,4"


def test_capture_mumu_window_frame_defaults_to_real_mumu_window_and_auto_mode(monkeypatch):
    calls = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(
        mumu,
        "find_window",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("default MuMu should use dedicated finder")),
    )

    class CapturingFakeCapture(_FakeCapture):
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(mumu, "WindowCapture", CapturingFakeCapture)

    mumu.capture_mumu_window_frame()

    assert calls
    args, _kwargs = calls[-1]
    assert args[0] == 123
    assert args[2] == mumu.DEFAULT_CAPTURE_MODE
    assert args[3] == "凡人修仙传：人界篇-Powered by MuMu模拟器"
    assert args[4] == "exact"


def test_target_mumu_main_window_rect_defaults_to_recorded_desktop_geometry(monkeypatch):
    monkeypatch.delenv(mumu.MUMU_MAIN_WINDOW_RECT_ENV, raising=False)

    assert mumu.DEFAULT_MUMU_MAIN_WINDOW_RECT == (-647, 43, 629, 1155)
    assert mumu._target_mumu_main_window_rect() == mumu.DEFAULT_MUMU_MAIN_WINDOW_RECT


def test_target_mumu_main_window_rect_allows_env_override(monkeypatch):
    monkeypatch.setenv(mumu.MUMU_MAIN_WINDOW_RECT_ENV, "10,20,930,1700")

    assert mumu._target_mumu_main_window_rect() == (10, 20, 930, 1700)


def test_normalize_mumu_desktop_window_size_applies_recorded_xywh(monkeypatch):
    calls = []
    monkeypatch.delenv(mumu.MUMU_MAIN_WINDOW_RECT_ENV, raising=False)
    monkeypatch.setitem(sys.modules, "win32con", SimpleNamespace(SWP_NOZORDER=4, SWP_NOACTIVATE=16))
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(SetWindowPos=lambda *args: calls.append(args)),
    )
    monkeypatch.setattr(
        mumu,
        "_find_mumu_desktop_main_window",
        lambda: {
            "hwnd": 123,
            "title": "凡人修仙传：人界篇-Powered by MuMu模拟器",
            "class": "Qt5156QWindowIcon",
            "window_rect": [3000, 10, 3910, 1676],
            "window_size_logical": [910, 1666],
            "extended_rect_physical": [3000, 10, 3904, 1652],
            "extended_size_physical": [904, 1642],
            "client_screen_rect_logical": [3002, 10, 3902, 1650],
            "client_size_logical": [900, 1640],
            "dpi": 144,
            "scale": 1.5,
        },
    )

    result = mumu.normalize_mumu_desktop_window_size(apply=True)

    x, y, width, height = mumu.DEFAULT_MUMU_MAIN_WINDOW_RECT
    assert result["target_window_rect"] == [x, y, x + width, y + height]
    assert result["target_main_size"] == [width, height]
    assert result["already_target"] is False
    assert result["ok"] is False  # An ignored SetWindowPos must not report success.
    assert result["applied"] is True
    assert calls[-1] == (123, None, x, y, width, height, 20)


def test_click_processed_point_does_not_fallback_to_window_when_adb_is_unavailable(monkeypatch):
    clicked = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_tap_mumu_adb", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ADB 端口不可用")))
    monkeypatch.setattr(mumu, "map_processed_point_to_raw_point", lambda *_args, **_kwargs: (11, 22))
    monkeypatch.setattr(mumu, "click_window_raw_point", lambda hwnd, area, point: clicked.append((hwnd, area, point)))

    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        mumu.click_mumu_window_processed_point(
            x=10,
            y=20,
            title="MuMu",
            input_backend="adb",
            fixed_width=900,
            fixed_height=1600,
        )

    assert clicked == []


def test_click_processed_point_defaults_to_adb_without_window_input(monkeypatch):
    clicked = []
    taps = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_tap_mumu_adb", lambda *args, **kwargs: taps.append((args, kwargs)) or {"input": "adb"})
    monkeypatch.setattr(mumu, "click_window_raw_point", lambda hwnd, area, point: clicked.append((hwnd, area, point)))

    result = mumu.click_mumu_window_processed_point(
        x=10,
        y=20,
        title="MuMu",
        fixed_width=900,
        fixed_height=1600,
    )

    assert result["input"] == "adb"
    assert taps
    assert clicked == []


def test_drag_processed_points_does_not_fallback_to_window_when_adb_is_unavailable(monkeypatch):
    dragged = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_swipe_mumu_adb", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ADB 端口不可用")))
    monkeypatch.setattr(mumu, "map_processed_point_to_raw_point", lambda point, **_kwargs: (point[0] + 1, point[1] + 2))
    monkeypatch.setattr(
        mumu,
        "drag_window_raw_points",
        lambda hwnd, area, start, end, duration_ms=300: dragged.append((hwnd, area, start, end, duration_ms)),
    )

    with pytest.raises(RuntimeError, match="ADB 端口不可用"):
        mumu.drag_mumu_window_processed_points(
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

    assert dragged == []


def test_drag_processed_points_defaults_to_adb_without_window_input(monkeypatch):
    dragged = []
    swipes = []
    _patch_window_fallback(monkeypatch)
    monkeypatch.setattr(mumu, "_swipe_mumu_adb", lambda *args, **kwargs: swipes.append((args, kwargs)) or {"input": "adb"})
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
        fixed_width=900,
        fixed_height=1600,
    )

    assert result["input"] == "adb"
    assert swipes
    assert dragged == []


def test_game_window2_click_service_defaults_to_adb(monkeypatch):
    calls = []
    monkeypatch.setattr(
        window_actions,
        "click_mumu_window_processed_point",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "input": kwargs.get("input_backend")},
    )

    result = window_actions.click_game_window2_service({"x": 10, "y": 20})

    assert result["input"] == "adb"
    assert calls[0]["input_backend"] == "adb"


def test_game_window2_drag_service_defaults_to_adb(monkeypatch):
    calls = []
    monkeypatch.setattr(
        window_actions,
        "drag_mumu_window_processed_points",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "input": kwargs.get("input_backend")},
    )

    result = window_actions.drag_game_window2_service({"start_x": 10, "start_y": 20, "end_x": 30, "end_y": 40})

    assert result["input"] == "adb"
    assert calls[0]["input_backend"] == "adb"


def test_run_mumu_adb_input_connects_tcp_serial_before_shell(monkeypatch):
    commands = []

    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[1] == "connect":
            return SimpleNamespace(returncode=0, stdout="connected", stderr="")
        if command[-2:] == ["wm", "size"]:
            return SimpleNamespace(returncode=0, stdout="Physical size: 900x1600", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu._run_mumu_adb_input("echo ok")

    assert commands[0][1:] == ["connect", "192.168.31.181:5555"]
    assert commands[1][1:3] == ["-s", "192.168.31.181:5555"]
    assert result["adb_serial"] == "192.168.31.181:5555"


def test_parse_adb_wm_size_prefers_active_override_resolution():
    assert mumu._parse_adb_wm_size(
        "Physical size: 900x1600\nOverride size: 1080x1920"
    ) == (1080, 1920)


def test_run_mumu_adb_input_reuses_fresh_probe_without_rechecking_transport(monkeypatch):
    commands = []
    events = []
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)

    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(
        mumu,
        "_ensure_mumu_adb_port_available",
        lambda: pytest.fail("fresh probe must skip the duplicate socket check"),
    )
    monkeypatch.setattr(
        mumu,
        "_append_mumu_device_health_event",
        lambda event, payload: events.append((event, payload)),
    )
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda command, **_kwargs: commands.append(command)
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = mumu._run_mumu_adb_input("input tap 1 2", prepared_probe=probe)

    assert commands == [["D:\\adb.exe", "-s", serial, "shell", "input tap 1 2"]]
    assert result["adb_size"] == "Physical size: 900x1600"
    timing = events[0][1]
    assert timing["attempts"][0]["prepared_probe_reuse"] is True
    assert set(timing["attempts"][0]["stages_seconds"]) == {"input"}
    assert "ensure" not in timing["stages_seconds"]


def test_run_mumu_adb_input_expired_probe_falls_back_to_full_verification(monkeypatch):
    commands = []
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)
    expired_at = (
        mumu.time.monotonic()
        - mumu._MUMU_ADB_PREPARED_PROBE_TTL_SECONDS
        - 1
    )
    token = str(probe["adb_prepared_probe_token"])
    with mumu._MUMU_ADB_PREPARED_PROBE_LOCK:
        mumu._MUMU_ADB_PREPARED_PROBES[token] = (
            serial,
            str(probe["adb_size"]),
            expired_at,
        )

    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[-2:] == ["wm", "size"]:
            return SimpleNamespace(returncode=0, stdout="Physical size: 900x1600", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    mumu._run_mumu_adb_input("input tap 1 2", prepared_probe=probe)

    assert [command[1] for command in commands] == ["connect", "-s", "-s"]


def test_run_mumu_adb_input_failed_probe_reuse_reconnects_and_rechecks_size(monkeypatch):
    commands = []
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)

    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu.time, "sleep", lambda _seconds: None)

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[-2:] == ["wm", "size"]:
            return SimpleNamespace(returncode=0, stdout="Physical size: 900x1600", stderr="")
        if command[-1] == "input tap 1 2":
            attempts = sum(item[-1] == "input tap 1 2" for item in commands)
            return SimpleNamespace(
                returncode=0 if attempts == 2 else 1,
                stdout="",
                stderr="transport reset" if attempts == 1 else "",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu._run_mumu_adb_input("input tap 1 2", prepared_probe=probe)

    assert result["adb_serial"] == serial
    assert [command[1] for command in commands] == ["-s", "disconnect", "connect", "-s", "-s"]
    assert sum(command[-1] == "input tap 1 2" for command in commands) == 2


def test_prepared_probe_token_can_only_be_consumed_once(monkeypatch):
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])

    assert mumu._validated_mumu_adb_prepared_probe(probe) is not None
    assert mumu._validated_mumu_adb_prepared_probe(probe) is None


def test_prepared_probe_token_has_only_one_concurrent_consumer(monkeypatch):
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])
    barrier = threading.Barrier(3)
    results = []

    def consume():
        barrier.wait()
        results.append(mumu._validated_mumu_adb_prepared_probe(probe))

    threads = [threading.Thread(target=consume) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result is not None for result in results) == 1


def test_close_mumu_adb_session_invalidates_outstanding_probe(monkeypatch):
    serial = "192.168.31.181:5555"
    probe = _registered_prepared_probe(serial)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: [serial])

    mumu._close_mumu_adb_session()

    assert mumu._validated_mumu_adb_prepared_probe(probe) is None


def test_tap_mumu_adb_passes_its_probe_as_immediate_verification(monkeypatch):
    calls = []
    probe = {
        "input": "adb-cli",
        "adb_serial": "192.168.31.181:5555",
        "adb_size": "Physical size: 900x1600",
        "adb_verified_at_monotonic": mumu.time.monotonic(),
    }

    def fake_input(command, **kwargs):
        calls.append((command, kwargs))
        return probe if command == "echo ok" else {"input": "adb-cli"}

    monkeypatch.setattr(mumu, "_run_mumu_adb_input", fake_input)

    mumu._tap_mumu_adb(10, 20, frame_width=900, frame_height=1600)

    assert calls == [
        ("echo ok", {}),
        ("input tap 10 20", {"prepared_probe": probe}),
    ]


def test_run_mumu_adb_input_logs_retry_stage_timings_without_changing_commands(monkeypatch):
    commands = []
    events = []

    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        mumu,
        "_append_mumu_device_health_event",
        lambda event, payload: events.append((event, payload)),
    )

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[1] in {"connect", "disconnect"}:
            return SimpleNamespace(returncode=0, stdout="connected", stderr="")
        if command[-1] == "wm size":
            return SimpleNamespace(returncode=0, stdout="Physical size: 900x1600", stderr="")
        input_attempts = sum(1 for item in commands if item[-1] == "input tap 1 2")
        return SimpleNamespace(
            returncode=0 if input_attempts > 1 else 1,
            stdout="",
            stderr="temporary failure" if input_attempts == 1 else "",
        )

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu._run_mumu_adb_input("input tap 1 2")

    assert result["adb_serial"] == "192.168.31.181:5555"
    assert [command[1] for command in commands] == [
        "connect",
        "-s",
        "-s",
        "disconnect",
        "connect",
        "-s",
        "-s",
    ]
    assert len(events) == 1
    event, timing = events[0]
    assert event == "adb_input_timing"
    assert timing["command_kind"] == "input_tap"
    assert "command" not in timing
    assert timing["outcome"] == "success"
    assert timing["stages_seconds"]["ensure"] >= 0
    assert len(timing["attempts"]) == 2
    assert timing["attempts"][0]["outcome"] == "failed"
    assert timing["attempts"][0]["failed_stage"] == "input"
    assert set(timing["attempts"][0]["stages_seconds"]) == {
        "connect",
        "wm_size",
        "input",
    }
    assert timing["attempts"][1]["outcome"] == "success"
    assert set(timing["attempts"][1]["stages_seconds"]) == {
        "disconnect",
        "retry_settle",
        "connect",
        "wm_size",
        "input",
    }


def test_run_mumu_adb_input_logs_ensure_failure_before_adb(monkeypatch):
    events = []
    monkeypatch.setattr(
        mumu,
        "_ensure_mumu_adb_port_available",
        lambda: (_ for _ in ()).throw(RuntimeError("port unavailable")),
    )
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda *_args, **_kwargs: pytest.fail("ensure failure must stop before adb"),
    )
    monkeypatch.setattr(
        mumu,
        "_append_mumu_device_health_event",
        lambda event, payload: events.append((event, payload)),
    )

    with pytest.raises(RuntimeError, match="port unavailable"):
        mumu._run_mumu_adb_input("input tap 1 2")

    assert len(events) == 1
    event, timing = events[0]
    assert event == "adb_input_timing"
    assert timing["command_kind"] == "input_tap"
    assert timing["outcome"] == "failed"
    assert timing["stages_seconds"]["ensure"] >= 0
    assert timing["attempts"] == []
    assert "port unavailable" in timing["error"]


def test_run_mumu_adb_input_falls_back_to_manager_when_adb_cannot_inject(monkeypatch):
    commands = []

    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(
        mumu,
        "_run_mumu_manager_input",
        lambda command, **kwargs: commands.append((command, kwargs))
        or {"input": "mumu-manager-sh", "adb_serial": kwargs["serial"], "vmindex": "1"},
    )

    def fake_run(command, **_kwargs):
        if command[1] == "connect":
            return SimpleNamespace(returncode=0, stdout="connected", stderr="")
        if command[-2:] == ["wm", "size"]:
            return SimpleNamespace(returncode=0, stdout="Physical size: 900x1600", stderr="")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission",
        )

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu._run_mumu_adb_input("input swipe 1 2 3 4 1000", timeout_s=7)

    assert result["input"] == "mumu-manager-sh"
    assert commands == [
        (
            "input swipe 1 2 3 4 1000",
            {"serial": "192.168.31.181:5555", "timeout_s": 7},
        )
    ]


def test_text_mumu_adb_uses_manager_unicode_input_for_chinese(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:7555"])
    monkeypatch.setattr(
        mumu,
        "_run_mumu_manager_input",
        lambda command, **kwargs: calls.append((command, kwargs))
        or {"input": "mumu-manager-sh", "adb_serial": kwargs["serial"], "vmindex": "1"},
    )
    monkeypatch.setattr(
        mumu,
        "_run_mumu_adb_input",
        lambda _command: pytest.fail("中文不得走 Android input text"),
    )

    result = mumu.text_mumu_adb("启学逢秋")

    assert calls == [("input_text 启学逢秋", {"serial": "127.0.0.1:7555", "timeout_s": 5})]
    assert result["unicode_input"] is True
    assert result["text_length"] == 4


def test_keyevents_mumu_adb_uses_manager_for_unicode_text_clear(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:7555"])
    monkeypatch.setattr(
        mumu,
        "_run_mumu_manager_input",
        lambda command, **kwargs: calls.append((command, kwargs))
        or {"input": "mumu-manager-sh", "adb_serial": kwargs["serial"], "vmindex": "1"},
    )
    monkeypatch.setattr(
        mumu,
        "_run_mumu_adb_input",
        lambda _command: pytest.fail("中文清空不得走批量 Android keyevent"),
    )

    result = mumu.keyevents_mumu_adb(["KEYCODE_MOVE_END", "KEYCODE_DEL", "KEYCODE_DEL"])

    assert calls == [
        ("key_delete", {"serial": "127.0.0.1:7555", "timeout_s": 5}),
        ("key_delete", {"serial": "127.0.0.1:7555", "timeout_s": 5}),
    ]
    assert result["unicode_delete"] is True
    assert result["delete_count"] == 2


def test_text_mumu_adb_keeps_ascii_on_android_input(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mumu,
        "_run_mumu_adb_input",
        lambda command: calls.append(command) or {"input": "adb"},
    )

    result = mumu.text_mumu_adb("ABC 123")

    assert calls == ["input text ABC%s123"]
    assert result["text_length"] == 7


def test_mumu_adb_port_check_recovers_local_port(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: [])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))

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
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: [])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])

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


def test_mumu_adb_port_check_accepts_mumu_manager_remote_candidate(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu.MUMU_ADB_PORT_PROBE_TIMEOUT_ENV, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: ["192.168.31.181:5555"])

    attempts = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_create_connection(address, timeout=0):
        attempts.append((address, timeout))
        if address[0] == "192.168.31.181":
            return FakeSocket()
        raise OSError("timed out")

    monkeypatch.setattr(mumu.socket, "create_connection", fake_create_connection)

    mumu._ensure_mumu_adb_port_available()

    assert (("192.168.31.181", 5555), 0.75) in attempts


def test_mumu_adb_failure_cache_does_not_block_fresh_mumu_manager_candidate(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    mumu._set_mumu_adb_failure_cache("ADB 端口不可用：127.0.0.1:7555: timed out")
    monkeypatch.setattr(mumu, "_recover_mumu_adb_ports", lambda: False)
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: ["192.168.31.181:5555"])

    attempts = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_create_connection(address, timeout=0):
        attempts.append(address)
        if address == ("192.168.31.181", 5555):
            return FakeSocket()
        raise OSError("timed out")

    monkeypatch.setattr(mumu.socket, "create_connection", fake_create_connection)

    mumu._ensure_mumu_adb_port_available()

    assert ("192.168.31.181", 5555) in attempts


def test_screencap_ignores_stale_adb_failure_cache(monkeypatch):
    mumu._clear_mumu_adb_failure_cache()
    mumu._set_mumu_adb_failure_cache("ADB 端口不可用：127.0.0.1:7555: timed out")

    calls = []

    def fake_session_shell(command, *, timeout_s=8):
        calls.append((command, timeout_s))
        return b"\x89PNG\r\n\x1a\nfake", {"input": "adb-session", "adb_serial": "192.168.31.181:5555"}

    monkeypatch.setattr(mumu, "_mumu_adb_session_shell_bytes", fake_session_shell)

    data, meta = mumu.screencap_mumu_adb_png()

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert meta["adb_serial"] == "192.168.31.181:5555"
    assert calls == [("screencap -p", 10)]
    assert mumu._get_mumu_adb_failure_cache() is None


def test_mumu_device_health_check_prefers_adb_and_uses_one_minute_cache(monkeypatch):
    mumu.reset_mumu_device_health_state()
    calls = []

    def fake_adb_health_info(vmindex="1"):
        calls.append(vmindex)
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        }

    monkeypatch.setattr(mumu, "_mumu_adb_health_info", fake_adb_health_info)
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("healthy ADB must bypass MuMuManager")),
    )
    monkeypatch.setattr(mumu, "_mumu_device_health_check_interval", lambda default=60.0: 60.0)

    first = mumu.mumu_device_health_check()
    second = mumu.mumu_device_health_check()

    assert first["status"] == "healthy"
    assert second["status"] == "healthy"
    assert calls == ["1"]


def test_mumu_adb_health_info_reports_online_without_manager(monkeypatch):
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["192.168.31.181:5555"])
    connections = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_create_connection(address, *, timeout):
        connections.append((address, timeout))
        return FakeSocket()

    monkeypatch.setattr(mumu.socket, "create_connection", fake_create_connection)

    info = mumu._mumu_adb_health_info("1")

    assert info == {
        "index": "1",
        "is_process_started": True,
        "is_android_started": True,
        "player_state": "start_finished",
        "adb_host_ip": "192.168.31.181",
        "adb_port": 5555,
        "health_source": "adb_socket",
    }
    assert connections == [(("192.168.31.181", 5555), mumu._mumu_adb_port_probe_timeout())]


def test_mumu_adb_failures_recover_only_after_three_recent_failures(monkeypatch):
    mumu.reset_mumu_device_health_state()
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        mumu._mumu_device_health_state.update({
            "status": "healthy",
            "checked_monotonic": mumu.time.monotonic(),
            "checked_at": mumu.time.time(),
        })

    checks = []
    recoveries = []
    monkeypatch.setattr(mumu, "_mumu_device_health_check_interval", lambda default=60.0: 60.0)
    monkeypatch.setattr(mumu, "mumu_device_health_check", lambda **_kwargs: checks.append(_kwargs) or {"status": "broken"})
    monkeypatch.setattr(mumu, "recover_mumu_device", lambda **_kwargs: recoveries.append(_kwargs) or {"status": "healthy", "recovered": True})

    mumu.record_mumu_adb_failure("first")
    mumu.record_mumu_adb_failure("second")
    result = mumu.record_mumu_adb_failure("third")

    assert checks == [{"vmindex": "1", "force": True}]
    assert recoveries == [{"vmindex": "1", "reason": "adb_failure:third", "force_restart": True}]
    assert result["recovered"] is True


def test_single_adb_timeout_does_not_restart_after_stale_health_check(monkeypatch):
    mumu.reset_mumu_device_health_state()
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        mumu._mumu_device_health_state.update({
            "status": "healthy",
            "checked_monotonic": mumu.time.monotonic() - 600,
            "checked_at": mumu.time.time() - 600,
        })

    checks = []
    recoveries = []
    monkeypatch.setattr(mumu, "_read_mumu_device_recovery_state", lambda: {})
    monkeypatch.setattr(mumu, "_mumu_device_last_recovery_at", lambda: 0.0)
    monkeypatch.setattr(mumu, "mumu_device_health_check", lambda **kwargs: checks.append(kwargs) or {"status": "healthy"})
    monkeypatch.setattr(mumu, "recover_mumu_device", lambda **kwargs: recoveries.append(kwargs) or {"recovered": True})

    result = mumu.record_mumu_adb_failure("Reading from 192.168.31.181:5555 timed out", recover=True)

    assert result["failure_count"] == 1
    assert result["status"] == "suspect"
    assert checks == []
    assert recoveries == []


def test_mumu_adb_failure_defers_recovery_during_startup_grace(monkeypatch):
    mumu.reset_mumu_device_health_state()
    now = mumu.time.time()
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        mumu._mumu_device_health_state.update({
            "status": "healthy",
            "last_recovery_at": now,
            "checked_at": now,
            "checked_monotonic": mumu.time.monotonic(),
        })

    recoveries = []
    monkeypatch.setattr(mumu, "_read_mumu_device_recovery_state", lambda: {})
    monkeypatch.setattr(mumu, "_mumu_device_startup_grace_seconds", lambda default=300.0: 300.0)
    monkeypatch.setattr(mumu, "recover_mumu_device", lambda **kwargs: recoveries.append(kwargs) or {"recovered": True})

    result = mumu.record_mumu_adb_failure("MuMu ADB截图疑似黑屏，需重建模拟器画面链路", recover=True)

    assert result["recovered"] is False
    assert result["recovery_deferred"] == "startup_grace"
    assert result["status"] == "starting"
    assert recoveries == []


def test_login_barrier_outlives_timed_startup_grace(monkeypatch):
    mumu.reset_mumu_device_health_state()
    now = 1_000.0
    monkeypatch.setattr(mumu, "_mumu_device_last_recovery_at", lambda: 600.0)
    monkeypatch.setattr(
        mumu,
        "_read_mumu_device_recovery_state",
        lambda: {"startup_grace_active": True},
    )
    monkeypatch.setattr(
        mumu,
        "_mumu_device_startup_grace_seconds",
        lambda default=300.0: 300.0,
    )

    state = mumu.mumu_device_startup_grace_state(now=now)

    assert state["active"] is False
    assert state["login_required"] is True


def test_black_frame_requires_sustained_observation_outside_startup(monkeypatch):
    mumu.reset_mumu_device_health_state()
    now = {"value": 1000.0}
    monkeypatch.setattr(mumu.time, "time", lambda: now["value"])
    monkeypatch.setattr(mumu, "_read_mumu_device_recovery_state", lambda: {})
    monkeypatch.setattr(mumu, "_mumu_device_last_recovery_at", lambda: 0.0)
    monkeypatch.setattr(mumu, "_mumu_frame_unusable_recovery_seconds", lambda default=30.0: 30.0)
    checks = []
    recoveries = []
    monkeypatch.setattr(mumu, "mumu_device_health_check", lambda **kwargs: checks.append(kwargs) or {"status": "broken"})
    monkeypatch.setattr(mumu, "recover_mumu_device", lambda **kwargs: recoveries.append(kwargs) or {"recovered": True})

    first = mumu.record_mumu_adb_failure("MuMu ADB截图疑似黑屏", recover=True)
    now["value"] += 10.0
    second = mumu.record_mumu_adb_failure("MuMu ADB截图疑似黑屏", recover=True)
    now["value"] += 21.0
    third = mumu.record_mumu_adb_failure("MuMu ADB截图疑似黑屏", recover=True)

    assert first["recovery_deferred"] == "frame_unusable_observation_window"
    assert first["frame_unusable_recovery_seconds"] == 30.0
    assert second["recovery_deferred"] == "frame_unusable_observation_window"
    assert second["frame_unusable_recovery_seconds"] == 30.0
    assert checks == [{"vmindex": "1", "force": True}]
    assert recoveries == [{"vmindex": "1", "reason": "adb_failure:MuMu ADB截图疑似黑屏", "force_restart": True}]
    assert third["recovered"] is True


def test_mumu_recovery_failure_keeps_startup_grace_active(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    recovery_state_path = tmp_path / "recovery_state.json"
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: recovery_state_path)
    monkeypatch.setattr(mumu, "_mumu_device_auto_recovery_enabled", lambda: True)
    monkeypatch.setattr(
        mumu,
        "mumu_device_health_check",
        lambda **_kwargs: {
            "status": "stopped",
            "info": {"is_process_started": False},
        },
    )
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("launch failed")))

    result = mumu.recover_mumu_device(reason="startup_failure")

    assert result["recovered"] is False
    assert result["startup_grace_active"] is True
    persisted = mumu.json.loads(recovery_state_path.read_text(encoding="utf-8"))
    assert persisted["startup_grace_active"] is True


def test_mumu_device_health_reports_starting_before_android_started(monkeypatch):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setattr(mumu, "_mumu_adb_health_info", lambda vmindex="1": None)
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": False,
            "player_state": "starting_rom",
        },
    )

    result = mumu.mumu_device_health_check(force=True)

    assert result["status"] == "starting"


def test_mumu_device_health_manager_started_but_adb_unreachable_is_broken(monkeypatch):
    mumu.reset_mumu_device_health_state()
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        mumu._mumu_device_health_state["failure_count"] = 2
    mumu._clear_mumu_adb_failure_cache()
    mumu._set_mumu_adb_failure_cache("ADB 端口不可用：127.0.0.1:7555: timed out")
    monkeypatch.setattr(mumu, "_mumu_adb_health_info", lambda vmindex="1": None)
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "0.0.0.0",
            "adb_port": 5555,
            "pid": 76700,
        },
    )

    result = mumu.mumu_device_health_check(force=True)

    assert result["status"] == "broken"
    assert result["last_error"] == "Android已启动但ADB端点不可达"
    assert result["failure_count"] == 2
    assert mumu._get_mumu_adb_failure_cache() is not None


def test_mumu_device_health_adb_observation_clears_failure_state(monkeypatch):
    mumu.reset_mumu_device_health_state()
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        mumu._mumu_device_health_state["failure_count"] = 3
    mumu._clear_mumu_adb_failure_cache()
    mumu._set_mumu_adb_failure_cache("ADB 端口不可用：127.0.0.1:7555: timed out")
    monkeypatch.setattr(
        mumu,
        "_mumu_adb_health_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        },
    )
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("healthy ADB must bypass MuMuManager")),
    )

    result = mumu.mumu_device_health_check(force=True)

    assert result["status"] == "healthy"
    assert result["failure_count"] == 0
    assert mumu._get_mumu_adb_failure_cache() is None


def test_mumu_manager_launch_uses_windows_task_scheduler_broker(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_mumu_manager_path", lambda: Path("MuMuManager.exe"))
    monkeypatch.setattr(mumu.os, "name", "nt")
    monkeypatch.setattr(
        mumu,
        "_launch_mumu_via_windows_task",
        lambda vmindex, path: calls.append((vmindex, path))
        or {"launch_broker": "windows_task_scheduler"},
    )

    result = mumu._mumu_manager_control("1", "launch")

    assert calls == [("1", Path("MuMuManager.exe"))]
    assert result["launch_broker"] == "windows_task_scheduler"


def test_windows_task_broker_creates_past_due_task_and_runs_it(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu._launch_mumu_via_windows_task("1", Path(r"C:\Program Files\MuMu\MuMuManager.exe"))

    assert calls[0][0][:5] == ["schtasks", "/Create", "/TN", r"\Fanxiu MuMu Launch 1", "/SC"]
    assert calls[0][0][calls[0][0].index("/ST") + 1] == "00:00"
    assert '"C:\\Program Files\\MuMu\\MuMuManager.exe"' in calls[0][0][calls[0][0].index("/TR") + 1]
    assert calls[1][0] == ["schtasks", "/Run", "/TN", r"\Fanxiu MuMu Launch 1"]
    assert result["launch_broker"] == "windows_task_scheduler"


def test_mumu_manager_non_launch_control_stays_in_caller_lifecycle(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_mumu_manager_path", lambda: Path("MuMuManager.exe"))
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda command, **kwargs: calls.append((command, kwargs))
        or SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    monkeypatch.setattr(
        mumu.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("ordinary control must not break away"),
    )

    mumu._mumu_manager_control("1", "shutdown")

    assert calls[0][0] == ["MuMuManager.exe", "control", "--vmindex", "1", "shutdown"]


def test_mumu_manager_discovery_is_cached_across_hot_path_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(mumu, "_mumu_manager_path", lambda: Path("MuMuManager.exe"))

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=mumu.json.dumps({
                "1": {
                    "index": "1",
                    "is_process_started": True,
                    "adb_host_ip": "192.168.31.181",
                    "adb_port": 5555,
                }
            }),
            stderr="",
        )

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    first = mumu._mumu_manager_adb_serial_candidates()
    second = mumu._mumu_manager_adb_serial_candidates()

    assert first == ["192.168.31.181:5555"]
    assert second == first
    assert len(calls) == 1
    assert mumu._read_mumu_manager_discovery_cache()["vmindex_by_serial"] == {
        "192.168.31.181:5555": "1"
    }


def test_mumu_manager_discovery_rejects_wildcard_adb_hosts():
    serials, vmindex_by_serial = mumu._mumu_manager_serials_from_info({
        "1": {
            "index": "1",
            "is_process_started": True,
            "adb_host_ip": "0.0.0.0",
            "adb_port": 5555,
        },
        "2": {
            "index": "2",
            "is_process_started": True,
            "adb_host_ip": "::",
            "adb_port": 5555,
        },
    })

    assert serials == []
    assert vmindex_by_serial == {}


def test_mumu_manager_discovery_cache_drops_wildcard_mapping():
    mumu._write_mumu_manager_discovery_cache(
        ["0.0.0.0:5555", "192.168.31.181:5555"],
        {"0.0.0.0:5555": "1", "192.168.31.181:5555": "1"},
    )

    persisted = mumu._read_mumu_manager_discovery_cache()
    assert persisted["serials"] == ["192.168.31.181:5555"]
    assert persisted["vmindex_by_serial"] == {"192.168.31.181:5555": "1"}


def test_mumu_manager_discovery_reuses_persisted_cache_without_spawning(monkeypatch):
    mumu._write_mumu_manager_discovery_cache(
        ["192.168.31.181:5555"],
        {"192.168.31.181:5555": "1"},
    )
    with mumu._MUMU_MANAGER_ADB_SERIAL_CACHE_LOCK:
        mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.clear()
        mumu._MUMU_MANAGER_VM_INDEX_CACHE.clear()
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("persisted cache must bypass MuMuManager")),
    )

    assert mumu._mumu_manager_adb_serial_candidates() == ["192.168.31.181:5555"]
    assert mumu._mumu_manager_vmindex_for_adb_serial("192.168.31.181:5555") == "1"


def test_mumu_manager_forced_refresh_respects_cross_process_cooldown(monkeypatch):
    mumu._write_mumu_manager_discovery_cache(
        ["192.168.31.181:5555"],
        {"192.168.31.181:5555": "1"},
    )
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fresh peer cache must coalesce refresh")),
    )

    assert mumu._mumu_manager_adb_serial_candidates(force_refresh=True) == ["192.168.31.181:5555"]


def test_mumu_manager_refresh_failure_keeps_stale_working_address(monkeypatch):
    mumu._write_mumu_manager_discovery_cache(
        ["192.168.31.181:5555"],
        {"192.168.31.181:5555": "1"},
        updated_at=mumu.time.time() - 3600,
    )
    monkeypatch.setattr(mumu, "_mumu_manager_path", lambda: Path("MuMuManager.exe"))
    monkeypatch.setattr(
        mumu,
        "run_quiet",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("MuMuManager crashed")),
    )

    assert mumu._mumu_manager_adb_serial_candidates(force_refresh=True) == ["192.168.31.181:5555"]


def test_mumu_device_health_events_are_written_as_jsonl(tmp_path):
    mumu._append_mumu_device_health_event("probe", {"status": "broken"}, include_resources=True, include_native=True)

    logs = list(tmp_path.glob("device-health-*.jsonl"))
    assert len(logs) == 1
    payload = mumu.json.loads(logs[0].read_text(encoding="utf-8").strip())
    assert payload["event"] == "probe"
    assert payload["status"] == "broken"
    assert payload["host_resources"]["memory"]["available_mb"] == 123
    assert payload["mumu_native"]["suspected_causes"] == ["probe"]


def test_host_resource_pressure_hints_detect_commit_and_winmgmt():
    snapshot = {
        "commit": {
            "commit_percent": 98.0,
            "commit_available_mb": 2048,
        },
        "top_private_processes": [
            {"name": "svchost.exe", "private_mb": 49152, "services": ["Winmgmt"]},
            {"name": "MuMuVMMHeadless.exe", "private_mb": 9216},
            {"name": "python.exe", "private_mb": 5120},
        ],
    }

    hints = mumu._host_resource_pressure_hints(snapshot)

    assert hints == [
        "large_svchost_commit",
        "mumu_commit_high",
        "python_commit_high",
        "windows_commit_nearly_exhausted",
        "winmgmt_wmi_commit_growth",
    ]


def test_windows_services_for_pid_parses_sc_queryex_output(monkeypatch):
    class Result:
        stdout = (
            "SERVICE_NAME: EventLog\n"
            "        PID                : 1668\n"
            "SERVICE_NAME: Winmgmt\n"
            "        PID                : 4312\n"
        )
        returncode = 0

    monkeypatch.setattr(mumu.os, "name", "nt")
    monkeypatch.setattr(mumu.subprocess, "run", lambda *_args, **_kwargs: Result())

    assert mumu._windows_services_for_pid(4312) == ["Winmgmt"]


def test_windows_services_for_pid_returns_empty_when_sc_queryex_fails(monkeypatch):
    class ScResult:
        stdout = ""
        returncode = 1

    monkeypatch.setattr(mumu.os, "name", "nt")
    monkeypatch.setattr(mumu.subprocess, "run", lambda *_args, **_kwargs: ScResult())

    assert mumu._windows_services_for_pid(4312) == []


def test_recover_mumu_device_does_not_cooldown_when_already_healthy(monkeypatch):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    controls = []
    closed_sessions = []

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: closed_sessions.append(True))
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
    monkeypatch.setattr(
        mumu,
        "_mumu_adb_health_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        },
    )
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        },
    )

    result = mumu.recover_mumu_device(reason="test")

    assert result["status"] == "healthy"
    assert result["recovered"] is False
    assert result["recovery_skipped"] == "already_healthy"
    assert controls == []
    assert closed_sessions == [True]
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        assert mumu._mumu_device_health_state["last_recovery_at"] == 0.0


def test_recover_mumu_device_uses_persisted_cooldown_after_process_restart(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    state_path = tmp_path / "recovery_state.json"
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: state_path)
    state_path.write_text(
        mumu.json.dumps(
            {
                "last_recovery_at": mumu.time.time(),
                "last_recovery_reason": "previous_process",
                "vmindex": "1",
            }
        ),
        encoding="utf-8",
    )
    controls = []

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": False,
            "player_state": "stopped",
        },
    )

    result = mumu.recover_mumu_device(reason="new_process")

    assert result["recovered"] is False
    assert result["recovery_skipped"] == "cooldown"
    assert controls == []


def test_recover_mumu_device_allows_stopped_instance_after_short_cooldown(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    state_path = tmp_path / "recovery_state.json"
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: state_path)
    state_path.write_text(
        mumu.json.dumps(
            {
                "last_recovery_at": mumu.time.time() - 60,
                "last_recovery_reason": "previous_process",
                "vmindex": "1",
            }
        ),
        encoding="utf-8",
    )
    controls = []
    lifecycle = []
    checks = {"count": 0}
    adb_calls = {"count": 0}

    def fake_adb_health_info(vmindex="1"):
        adb_calls["count"] += 1
        if adb_calls["count"] == 1:
            return None
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        }

    monkeypatch.setattr(mumu, "_mumu_adb_health_info", fake_adb_health_info)

    def fake_player_info(vmindex="1"):
        checks["count"] += 1
        if checks["count"] == 1:
            return {
                "index": str(vmindex),
                "is_process_started": False,
                "is_android_started": False,
                "player_state": "stopped",
            }
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        }

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: None)
    monkeypatch.setattr(
        mumu, "_terminate_orphaned_mumu_vm_processes",
        lambda vmindex: lifecycle.append(("cleanup", vmindex)) or [39040],
    )
    monkeypatch.setattr(
        mumu, "configure_mumu_display",
        lambda **kwargs: lifecycle.append(("native_display", kwargs)) or {"changed": False},
    )
    monkeypatch.setattr(
        mumu,
        "_schedule_login_job_after_mumu_restart",
        lambda **_kwargs: lifecycle.append("login_intent") or {
            "ok": True, "task_id": "login-game", "next_time": "now"
        },
    )
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_control",
        lambda *args, **kwargs: (
            lifecycle.append("vm_control"), controls.append((args, kwargs)), {}
        )[-1],
    )
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "wait_mumu_adb_online", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "ensure_mumu_adb_resolution", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "normalize_mumu_desktop_window_size", lambda **_kwargs: {"ok": True, "already_target": True})
    monkeypatch.setattr(mumu, "_mumu_manager_launch_app", lambda *_args, **_kwargs: {"errcode": 0})
    monkeypatch.setattr(mumu, "wait_mumu_recovery_frame_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)

    result = mumu.recover_mumu_device(reason="new_process")

    assert result["recovered"] is True
    assert controls == [(("1", "launch"), {"timeout": 15})]
    assert lifecycle == [
        "login_intent", ("cleanup", "1"),
        ("native_display", {"width": 900, "height": 1600, "dpi": 320, "vmindex": "1"}),
        "vm_control", "login_intent",
    ]
    assert result["terminated_orphaned_process_ids"] == [39040]
    assert result["login_scheduler"]["task_id"] == "login-game"


def test_terminate_orphaned_mumu_vm_processes_only_targets_requested_index(monkeypatch):
    calls = []

    class FakeProcess:
        def __init__(self, pid, command):
            self.pid = pid
            self.info = {"pid": pid, "name": "MuMuNxDevice.exe", "cmdline": command}

        def terminate(self):
            calls.append(("terminate", self.pid))

        def kill(self):
            calls.append(("kill", self.pid))

    processes = [
        FakeProcess(101, ["MuMuNxDevice.exe", "-v", "1"]),
        FakeProcess(202, ["MuMuNxDevice.exe", "-v", "2"]),
        FakeProcess(303, ["other.exe", "-v", "1"]),
    ]
    processes[2].info["name"] = "other.exe"
    monkeypatch.setattr(mumu.psutil, "process_iter", lambda _attrs: processes)
    monkeypatch.setattr(mumu.psutil, "wait_procs", lambda items, timeout: (list(items), []))

    result = mumu._terminate_orphaned_mumu_vm_processes("1")

    assert result == [101]
    assert calls == [("terminate", 101)]


def test_force_restart_cleans_target_process_after_manager_shutdown(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: tmp_path / "recovery_state.json")
    checks = {"count": 0}
    controls = []
    cleaned = []

    def fake_adb_health_info(vmindex="1"):
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        }

    monkeypatch.setattr(mumu, "_mumu_adb_health_info", fake_adb_health_info)

    def fake_player_info(vmindex="1"):
        checks["count"] += 1
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        }

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
    monkeypatch.setattr(
        mumu,
        "_terminate_orphaned_mumu_vm_processes",
        lambda vmindex: cleaned.append(vmindex) or [39040],
    )
    monkeypatch.setattr(mumu, "wait_mumu_adb_online", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "ensure_mumu_adb_resolution", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "normalize_mumu_desktop_window_size", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "_mumu_manager_launch_app", lambda *_args, **_kwargs: {"errcode": 0})
    monkeypatch.setattr(mumu, "wait_mumu_recovery_frame_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)

    result = mumu.recover_mumu_device(reason="maintenance", force_restart=True)

    assert cleaned == ["1"]
    assert controls[:2] == [
        (("1", "shutdown"), {"timeout": 15}),
        (("1", "launch"), {"timeout": 15}),
    ]
    assert result["terminated_orphaned_process_ids"] == [39040]


def test_ensure_mumu_adb_resolution_repairs_wrong_wm_size(monkeypatch):
    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:7555"])
    commands = []
    state = {"size": "Physical size: 720x1280", "density": "Physical density: 240"}

    def fake_run(command, **_kwargs):
        commands.append(tuple(str(part) for part in command))
        shell = " ".join(str(part) for part in command)
        if "wm size 900x1600" in shell:
            state["size"] = "Physical size: 900x1600"
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "wm density 320" in shell:
            state["density"] = "Physical density: 320"
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if shell.endswith("shell wm size"):
            return SimpleNamespace(returncode=0, stdout=state["size"], stderr="")
        if shell.endswith("shell wm density"):
            return SimpleNamespace(returncode=0, stdout=state["density"], stderr="")
        return SimpleNamespace(returncode=0, stdout="connected", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = mumu.ensure_mumu_adb_resolution(vmindex="1")

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["before"]["size"] == "Physical size: 720x1280"
    assert result["after"]["size"] == "Physical size: 900x1600"
    assert any("wm size 900x1600" in " ".join(command) for command in commands)
    assert any("wm density 320" in " ".join(command) for command in commands)


def test_ensure_mumu_adb_root_restarts_adbd_and_verifies_identity(monkeypatch):
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "adb_path", lambda: Path("D:/adb.exe"))
    monkeypatch.setattr(mumu, "_mumu_adb_serial_candidates", lambda: ["127.0.0.1:7555"])
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(tuple(str(part) for part in command))
        if tuple(command[-2:]) == ("shell", "id"):
            return SimpleNamespace(returncode=0, stdout="uid=0(root) gid=0(root)", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mumu, "run_quiet", fake_run)

    result = _REAL_ENSURE_MUMU_ADB_ROOT(vmindex="1")

    assert result["ok"] is True
    assert "uid=0(root)" in result["identity"]
    assert ("-s", "127.0.0.1:7555", "root") in [command[1:] for command in commands]
    assert ("-s", "127.0.0.1:7555", "shell", "id") in [command[1:] for command in commands]


def test_recover_mumu_device_records_resolution_check(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: tmp_path / "recovery_state.json")
    checks = {"count": 0}
    adb_calls = {"count": 0}

    def fake_adb_health_info(vmindex="1"):
        adb_calls["count"] += 1
        if adb_calls["count"] == 1:
            return None
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        }

    monkeypatch.setattr(mumu, "_mumu_adb_health_info", fake_adb_health_info)

    def fake_player_info(vmindex="1"):
        checks["count"] += 1
        if checks["count"] == 1:
            return {
                "index": str(vmindex),
                "is_process_started": False,
                "is_android_started": False,
                "player_state": "stopped",
            }
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        }

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "wait_mumu_adb_online", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "_mumu_manager_launch_app", lambda *_args, **_kwargs: {"errcode": 0})
    monkeypatch.setattr(mumu, "wait_mumu_recovery_frame_ready", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        mumu,
        "ensure_mumu_adb_resolution",
        lambda **_kwargs: {"ok": True, "changed": False, "after": {"size": "Physical size: 900x1600"}},
    )
    monkeypatch.setattr(
        mumu,
        "normalize_mumu_desktop_window_size",
        lambda **_kwargs: {"ok": True, "already_target": False, "applied": True},
    )
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)

    result = mumu.recover_mumu_device(reason="resolution_probe")

    assert result["recovered"] is True
    assert result["resolution"]["ok"] is True
    assert result["window_size"]["applied"] is True
    with mumu._MUMU_DEVICE_HEALTH_LOCK:
        assert mumu._mumu_device_health_state["resolution"]["ok"] is True
    assert mumu._mumu_device_health_state["window_size"]["ok"] is True


def test_recover_mumu_device_rejects_process_healthy_when_frame_stays_black(monkeypatch, tmp_path):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    monkeypatch.setattr(mumu, "_mumu_device_recovery_state_path", lambda: tmp_path / "recovery_state.json")
    checks = {"count": 0}
    adb_calls = {"count": 0}

    def fake_adb_health_info(vmindex="1"):
        adb_calls["count"] += 1
        if adb_calls["count"] == 1:
            return None
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
            "adb_host_ip": "127.0.0.1",
            "adb_port": 7555,
            "health_source": "adb_socket",
        }

    monkeypatch.setattr(mumu, "_mumu_adb_health_info", fake_adb_health_info)

    def fake_player_info(vmindex="1"):
        checks["count"] += 1
        return {
            "index": str(vmindex),
            "is_process_started": checks["count"] > 1,
            "is_android_started": checks["count"] > 1,
            "player_state": "start_finished" if checks["count"] > 1 else "stopped",
        }

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: None)
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "wait_mumu_adb_online", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "ensure_mumu_adb_resolution", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(mumu, "_mumu_manager_launch_app", lambda *_args, **_kwargs: {"errcode": 0})
    monkeypatch.setattr(mumu, "normalize_mumu_desktop_window_size", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(
        mumu,
        "wait_mumu_recovery_frame_ready",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("frame stays black")),
    )
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)

    result = mumu.recover_mumu_device(reason="black_frame_probe")

    assert result["recovered"] is False
    assert result["status"] == "broken"
    assert "frame stays black" in result["last_error"]
    persisted = mumu.json.loads((tmp_path / "recovery_state.json").read_text(encoding="utf-8"))
    assert persisted["startup_grace_active"] is True


def test_recover_mumu_device_skips_auto_recovery_when_disabled(monkeypatch):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "0")
    controls = []

    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
    monkeypatch.setattr(
        mumu,
        "_mumu_manager_player_info",
        lambda vmindex="1": {
            "index": str(vmindex),
            "is_process_started": False,
            "is_android_started": False,
            "player_state": "stopped",
        },
    )

    result = mumu.recover_mumu_device(reason="test")

    assert result["status"] == "stopped"
    assert result["recovered"] is False
    assert result["recovery_skipped"] == "auto_recovery_disabled"
    assert controls == []


def test_mumu_adb_inject_events_error_is_treated_as_unavailable():
    message = (
        "ADB 输入失败：192.168.31.181:5555: Exception occurred while executing 'swipe':\n"
        "java.lang.SecurityException: Injecting to another application requires INJECT_EVENTS permission"
    )

    assert mumu._is_mumu_adb_unavailable_error(message)


def test_mumu_adb_connection_reset_is_treated_as_unavailable():
    assert mumu._is_mumu_adb_unavailable_error("[WinError 10054] 远程主机强迫关闭了一个现有的连接。")


def test_mumu_adb_session_ignores_cached_proxy_device_by_default(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    mumu._clear_mumu_adb_failure_cache()
    mumu._MUMU_ADB_SESSION.update({"device": object(), "host": "192.168.31.181", "port": 5555, "serial": "192.168.31.181:5555"})
    monkeypatch.setattr(mumu, "_ensure_mumu_adb_port_available", lambda: None)
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "devices", lambda: ["192.168.31.181:5555"])
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: [])

    attempted = []

    class FakeDevice:
        def __init__(self, host, port, **_kwargs):
            self.host = host
            self.port = port
            attempted.append(f"{host}:{port}")

        def connect(self, **_kwargs):
            if self.host != "127.0.0.1" or self.port != 7555:
                raise RuntimeError("unexpected serial")

        def shell(self, *_args, **_kwargs):
            return b"\x89PNG\r\n\x1a\nfake"

        def close(self):
            pass

    monkeypatch.setattr(mumu, "AdbDeviceTcp", None, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "adb_shell.adb_device", SimpleNamespace(AdbDeviceTcp=FakeDevice))

    data, meta = mumu._mumu_adb_session_shell_bytes("screencap -p")

    assert data.startswith(b"\x89PNG")
    assert attempted[0] == "127.0.0.1:7555"
    assert meta["adb_serial"] == "127.0.0.1:7555"


def test_mumu_adb_candidates_keep_cached_manager_device_during_transient_manager_failure(monkeypatch):
    for key in mumu.MUMU_ADB_SERIAL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv(mumu.MUMU_ADB_ALLOW_PROXY_DEVICES_ENV, raising=False)
    mumu._MUMU_ADB_SESSION.clear()
    with mumu._MUMU_MANAGER_ADB_SERIAL_CACHE_LOCK:
        mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.clear()
        mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.add("192.168.31.181:5555")
    mumu._MUMU_ADB_SESSION["serial"] = "192.168.31.181:5555"
    monkeypatch.setattr(mumu, "_mumu_manager_adb_serial_candidates", lambda: [])
    monkeypatch.setattr(mumu.fanxiu_adb_device_service, "devices", lambda: [])

    try:
        assert mumu._mumu_adb_serial_candidates() == [
            "192.168.31.181:5555",
            "127.0.0.1:7555",
            "127.0.0.1:16416",
            "127.0.0.1:5555",
        ]
    finally:
        mumu._MUMU_ADB_SESSION.clear()
        with mumu._MUMU_MANAGER_ADB_SERIAL_CACHE_LOCK:
            mumu._MUMU_MANAGER_ADB_SERIAL_CACHE.clear()
