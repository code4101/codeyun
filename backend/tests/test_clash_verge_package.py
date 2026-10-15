from zipfile import ZipFile

import pytest
import yaml

from scripts.package_clash_verge import APP_ID, build_portable_archive


def test_portable_snapshot_preserves_subscriptions_and_excludes_host_history(tmp_path):
    install, config = tmp_path / "install", tmp_path / "config"
    install.mkdir()
    (install / "resources").mkdir()
    (install / "resources" / "core.dat").write_bytes(b"data")
    for name in ("clash-verge.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe", "uninstall.exe"):
        (install / name).write_bytes(b"executable")
    config.mkdir()
    (config / "profiles").mkdir()
    profiles = {"current": "subscription", "items": [{"uid": "subscription", "type": "remote",
                "file": "subscription.yaml", "url": "https://example.test/private-token",
                "selected": [{"name": "Proxy", "now": "Node"}]}]}
    (config / "profiles.yaml").write_text(yaml.safe_dump(profiles))
    (config / "profiles" / "subscription.yaml").write_text("proxies: []")
    (config / "profiles" / "old.yaml.bak").write_text("old secret")
    (config / "verge.yaml").write_text("enable_auto_launch: true\nenable_system_proxy: true\n")
    (config / "config.yaml").write_text("mode: rule")
    (config / "clash-verge.yaml").write_text("proxies: []")
    (config / "logs").mkdir()
    (config / "logs" / "private.log").write_text("history")
    license_file = tmp_path / "LICENSE"
    license_file.write_text("license")
    output = tmp_path / "portable.zip"
    build_portable_archive(install, config, output, license_file=license_file)
    home = f"Clash-Verge/.config/{APP_ID}/"
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "Clash-Verge/.config/PORTABLE" in archive.namelist()
        assert not any("uninstall" in name or "private.log" in name or ".bak" in name for name in archive.namelist())
        assert yaml.safe_load(archive.read(home + "profiles.yaml")) == profiles
        copied = yaml.safe_load(archive.read(home + "verge.yaml"))
        assert copied == {"enable_auto_launch": False, "enable_system_proxy": True}
    assert yaml.safe_load((config / "verge.yaml").read_text())["enable_auto_launch"] is True
    profiles["items"][0]["file"] = "../secret.yaml"
    (config / "profiles.yaml").write_text(yaml.safe_dump(profiles))
    with pytest.raises(ValueError, match="filename"):
        build_portable_archive(install, config, tmp_path / "bad.zip", license_file=license_file)
