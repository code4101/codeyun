from types import SimpleNamespace
from pathlib import Path

import pytest

import backend.core.fanxiu.game.window_actions as window_actions
import backend.core.fanxiu.runtime.mumu_control as mumu


@pytest.fixture(autouse=True)
def _patch_mumu_device_health_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(mumu, "_mumu_device_health_log_dir", lambda: tmp_path)
    monkeypatch.setattr(mumu, "_collect_mumu_host_resource_snapshot", lambda: {"memory": {"available_mb": 123}})
    monkeypatch.setattr(
        mumu,
        "_collect_mumu_native_diagnostics",
        lambda: {"suspected_causes": ["probe"], "marker_lines": [{"file": "shell.log", "line": "VERR_TEST"}]},
    )


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
    monkeypatch.setattr(mumu.fanxiu_android_proxy_service, "adb_path", lambda: Path("D:/adb.exe"))

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


def test_mumu_device_health_check_uses_one_minute_cache(monkeypatch):
    mumu.reset_mumu_device_health_state()
    calls = []

    def fake_player_info(vmindex="1"):
        calls.append(vmindex)
        return {
            "index": str(vmindex),
            "is_process_started": True,
            "is_android_started": True,
            "player_state": "start_finished",
        }

    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "_mumu_device_health_check_interval", lambda default=60.0: 60.0)

    first = mumu.mumu_device_health_check()
    second = mumu.mumu_device_health_check()

    assert first["status"] == "healthy"
    assert second["status"] == "healthy"
    assert calls == ["1"]


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


def test_windows_services_for_pid_falls_back_to_tasklist(monkeypatch):
    class ScResult:
        stdout = ""
        returncode = 1

    class TasklistResult:
        stdout = (
            "Image Name                     PID Services\n"
            "========================= ======== ============================================\n"
            "svchost.exe                   4312 Winmgmt, EventLog\n"
        )
        returncode = 0

    results = [ScResult(), TasklistResult()]

    monkeypatch.setattr(mumu.os, "name", "nt")
    monkeypatch.setattr(mumu.subprocess, "run", lambda *_args, **_kwargs: results.pop(0))

    assert mumu._windows_services_for_pid(4312) == ["EventLog", "Winmgmt"]


def test_recover_mumu_device_does_not_cooldown_when_already_healthy(monkeypatch):
    mumu.reset_mumu_device_health_state()
    monkeypatch.setenv(mumu.MUMU_DEVICE_AUTO_RECOVERY_ENV, "1")
    controls = []
    closed_sessions = []

    monkeypatch.setattr(mumu, "_close_mumu_adb_session", lambda: closed_sessions.append(True))
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
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
    checks = {"count": 0}

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
    monkeypatch.setattr(mumu, "_mumu_manager_control", lambda *args, **kwargs: controls.append((args, kwargs)) or {})
    monkeypatch.setattr(mumu, "_mumu_manager_player_info", fake_player_info)
    monkeypatch.setattr(mumu, "_mumu_manager_launch_app", lambda *_args, **_kwargs: {"errcode": 0})
    monkeypatch.setattr(mumu.time, "sleep", lambda *_args, **_kwargs: None)

    result = mumu.recover_mumu_device(reason="new_process")

    assert result["recovered"] is True
    assert controls == [(("1", "launch"), {"timeout": 15})]


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
    monkeypatch.setattr(mumu.fanxiu_android_proxy_service, "devices", lambda: ["192.168.31.181:5555"])
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
