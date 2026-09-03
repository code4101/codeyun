from __future__ import annotations

from backend.core.fanxiu.client import adb_device


def test_adb_path_reuses_existing_cached_discovery(tmp_path, monkeypatch):
    executable = tmp_path / "adb.exe"
    executable.touch()
    monkeypatch.setenv("FANXIU_ADB_PATH", str(executable))
    service = adb_device.FanxiuAdbDeviceService()

    assert service.adb_path() == executable
    monkeypatch.setattr(
        adb_device.shutil,
        "which",
        lambda _name: (_ for _ in ()).throw(AssertionError("cached lookup must skip PATH discovery")),
    )

    assert service.adb_path() == executable


def test_adb_path_invalidates_cache_when_explicit_environment_changes(tmp_path, monkeypatch):
    first = tmp_path / "first-adb.exe"
    second = tmp_path / "second-adb.exe"
    first.touch()
    second.touch()
    service = adb_device.FanxiuAdbDeviceService()

    monkeypatch.setenv("FANXIU_ADB_PATH", str(first))
    assert service.adb_path() == first
    monkeypatch.setenv("FANXIU_ADB_PATH", str(second))

    assert service.adb_path() == second


def test_adb_path_invalidates_cache_when_executable_disappears(tmp_path, monkeypatch):
    executable = tmp_path / "adb.exe"
    executable.touch()
    monkeypatch.setenv("FANXIU_ADB_PATH", str(executable))
    monkeypatch.setattr(adb_device.shutil, "which", lambda _name: None)
    monkeypatch.setattr(adb_device, "DEFAULT_ADB_CANDIDATES", ())
    service = adb_device.FanxiuAdbDeviceService()

    assert service.adb_path() == executable
    executable.unlink()

    try:
        service.adb_path()
    except RuntimeError as exc:
        assert "找不到 adb.exe" in str(exc)
    else:
        raise AssertionError("missing cached executable must trigger rediscovery failure")
