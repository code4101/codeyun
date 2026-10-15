from io import BytesIO
from zipfile import ZipFile

import pytest
import yaml

from scripts.package_clash_verge import APP_ID
from scripts.package_clash_verge_macos import BACKUP_NAME, build_macos_backup, build_macos_package


@pytest.fixture
def windows_snapshot(tmp_path):
    home = f"Clash-Verge/.config/{APP_ID}/"
    profiles = {"current": "sub", "items": [{"uid": "sub", "file": "sub.yaml",
                "url": "https://example.test/token", "selected": [{"name": "Proxy", "now": "Node"}]}]}
    source = tmp_path / "windows.zip"
    with ZipFile(source, "w") as archive:
        for name, data in {
            "profiles.yaml": profiles,
            "config.yaml": {"mode": "rule", "mixed-port": 7897, "external-controller-pipe": "win-pipe",
                            "tun": {"enable": True, "device": "WindowsDevice"}},
            "verge.yaml": {"enable_auto_launch": True, "enable_tun_mode": True, "enable_system_proxy": True,
                           "webdav_password": "unrelated-secret", "hotkeys": ["win-shortcut"]},
            "dns_config.yaml": {"enable": True},
        }.items():
            archive.writestr(home + name, yaml.safe_dump(data))
        archive.writestr(home + "profiles/sub.yaml", "proxies: []")
        archive.writestr(home + "profiles/old.yaml.bak", "old secret")
        archive.writestr(home + "logs/history.log", "history")
        archive.writestr("Clash-Verge/clash-verge.exe", b"windows exe")
    return source, profiles


def test_macos_backup_uses_native_format_and_preserves_subscription(windows_snapshot):
    source, profiles = windows_snapshot
    before = source.read_bytes()
    backup = build_macos_backup(source)
    with ZipFile(BytesIO(backup)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"profiles/", "profiles/sub.yaml", "profiles.yaml", "config.yaml", "verge.yaml", "dns_config.yaml"}
        assert yaml.safe_load(archive.read("profiles.yaml")) == profiles
        core = yaml.safe_load(archive.read("config.yaml"))
        assert core["mode"] == "rule" and core["mixed-port"] == 7897
        assert "external-controller-pipe" not in core
        assert core["tun"] == {"enable": False}
        verge = yaml.safe_load(archive.read("verge.yaml"))
        assert verge["enable_system_proxy"] is True
        assert verge["enable_auto_launch"] is False and verge["enable_tun_mode"] is False
        assert "webdav_password" not in verge and "hotkeys" not in verge
    assert source.read_bytes() == before


def test_macos_distribution_keeps_official_dmg_unchanged(windows_snapshot, tmp_path):
    source, _ = windows_snapshot
    dmg = tmp_path / "official.dmg"
    dmg.write_bytes(b"unmodified signed disk image")
    output = build_macos_package(dmg, build_macos_backup(source), tmp_path / "mac.zip", architecture="aarch64")
    with ZipFile(output) as archive:
        assert archive.read(dmg.name) == dmg.read_bytes()
        assert BACKUP_NAME in archive.namelist()
        assert "\u5bfc\u5165" in archive.read("使用说明.txt").decode("utf-8")


def test_macos_backup_rejects_profile_traversal(windows_snapshot):
    source, profiles = windows_snapshot
    profiles["items"][0]["file"] = "../private.yaml"
    home = f"Clash-Verge/.config/{APP_ID}/"
    with ZipFile(source, "w") as archive:
        for name in ("profiles.yaml", "config.yaml", "verge.yaml"):
            archive.writestr(home + name, yaml.safe_dump(profiles if name == "profiles.yaml" else {}))
    with pytest.raises(ValueError, match="filename"):
        build_macos_backup(source)
