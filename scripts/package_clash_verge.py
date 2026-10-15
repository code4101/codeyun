"""Build a Windows Clash Verge Rev 2.5.2 portable snapshot for superuser download.

Run from the repository root with ``uv run python -m scripts.package_clash_verge
--help``. The version's upstream dirs.rs defines .config/PORTABLE and the nested
APP_ID directory. Only executable resources and current configuration references
are included; logs, backups, updater caches and uninstallers stay on the host.
Subscription URLs/node credentials intentionally remain in this private bundle.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

import yaml

from backend.core.resources.admin_downloads import publish_download_bundle

APP_ID = "io.github.clash-verge-rev.clash-verge-rev"
CONFIG_FILES = ("config.yaml", "verge.yaml", "profiles.yaml", "dns_config.yaml",
                "clash-verge.yaml", "clash-verge-check.yaml", "Country.mmdb", "geoip.dat", "geosite.dat")


def build_portable_archive(install: Path, config: Path, output: Path, *, license_file: Path) -> Path:
    """Snapshot current files, fail on missing references or concurrent config edits.

    Caller provides trusted local directories. Profile filenames are confined to
    profiles/ even when metadata is malformed. Original configuration is untouched.
    Autostart is disabled in the copy because it registers a host-specific path.
    """
    install, config = install.resolve(strict=True), config.resolve(strict=True)
    snapshots: dict[Path, bytes] = {}

    def snapshot(path: Path) -> bytes:
        data = path.read_bytes()
        snapshots[path] = data
        return data

    profiles = yaml.safe_load(snapshot(config / "profiles.yaml"))
    profile_paths = []
    for item in profiles["items"]:
        name = item.get("file")
        if not name:
            continue
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Profile reference must be a filename")
        path = (config / "profiles" / name).resolve(strict=True)
        if path.parent != (config / "profiles").resolve():
            raise ValueError("Profile reference escapes profiles directory")
        profile_paths.append(path)
    if profiles.get("current") not in {item.get("uid") for item in profiles["items"]}:
        raise ValueError("Current subscription is missing")
    prefix = "Clash-Verge/"
    home = prefix + ".config/" + APP_ID + "/"
    entries: dict[str, bytes | Path] = {}
    for name in ("clash-verge.exe", "verge-mihomo.exe", "verge-mihomo-alpha.exe"):
        path = install / name
        if not path.is_file():
            raise FileNotFoundError(name)
        entries[prefix + name] = path
    for path in (install / "resources").rglob("*"):
        if path.is_file():
            if path.is_symlink():
                raise ValueError("Linked executable resources are unsupported")
            entries[prefix + path.relative_to(install).as_posix()] = path
    for name in CONFIG_FILES:
        path = config / name
        if not path.exists():
            if name in ("config.yaml", "verge.yaml", "profiles.yaml", "clash-verge.yaml"):
                raise FileNotFoundError(name)
            continue
        data = snapshots[path] if path in snapshots else snapshot(path)
        if path.suffix == ".yaml":
            value = yaml.safe_load(data)
            if name == "verge.yaml":
                if value.get("startup_script"):
                    raise ValueError("A custom startup script needs a portability review")
                value["enable_auto_launch"] = False
                data = yaml.safe_dump(value, allow_unicode=True, sort_keys=False).encode("utf-8")
        entries[home + name] = data
    for path in profile_paths:
        entries[home + "profiles/" + path.name] = snapshot(path)
    # Read all live configs before writing, then detect source changes as one snapshot.
    if any(path.read_bytes() != data for path, data in snapshots.items()):
        raise RuntimeError("Configuration changed during snapshot; retry packaging")
    entries[prefix + ".config/PORTABLE"] = b""
    entries[prefix + "LICENSE"] = license_file.read_bytes()
    entries[prefix + "README.txt"] = (
        "Clash Verge Rev 2.5.2 / Windows x64 / CodeYun configuration snapshot\n\n"
        "1. Extract the entire Clash-Verge folder to a writable local directory.\n"
        "2. Close any other Clash Verge instance, then run clash-verge.exe.\n"
        "3. Current subscriptions, rules, proxy mode and saved node selections are included.\n"
        "   System proxy is enabled as in the source configuration.\n"
        "Windows 10/11 x64 with Microsoft Edge WebView2 Runtime is required.\n"
        "TUN/service mode may require administrator rights and service installation.\n"
        "Autostart is disabled; enable it in Settings on the destination PC if needed.\n"
        "Keep .config/PORTABLE and .config together with the executable.\n"
        "Logs, old backups and transient traffic/DNS cache are excluded.\n"
        "This private snapshot includes subscription URLs and node credentials.\n"
        "Recipients share subscription quota; distribute only to intended recipients.\n"
        "This snapshot does not sync future changes from the original PC.\n\n"
        "Upstream source and license: https://github.com/clash-verge-rev/clash-verge-rev/tree/v2.5.2\n"
        "Mihomo source: https://github.com/MetaCubeX/mihomo\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in entries.items():
            if isinstance(data, Path):
                archive.write(data, name)
            else:
                archive.writestr(name, data)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--license-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish-id", help="Publish immutable software bundle after building")
    args = parser.parse_args()
    if args.publish_id and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}", args.publish_id):
        parser.error("Invalid publish ID")
    path = build_portable_archive(args.install_dir, args.config_dir, args.output, license_file=args.license_file)
    if args.publish_id:
        result = publish_download_bundle(
            args.publish_id, title="Clash Verge Rev · 本机配置便携包", files=[path],
            category="software", version="2.5.2", platform="Windows 10/11 · x64",
            description="解压后运行 clash-verge.exe，已包含本机当前订阅、规则、代理模式和已保存的节点选择。\n"
                        "包含订阅凭据，接收者共用订阅额度；仅向指定人员分发。此包为配置快照。\n"
                        "需 WebView2 运行环境；TUN/服务模式可能需要管理员权限。便携包默认关闭开机启动。")
        print(f"Published {result['id']}: {result['files'][0]['bytes']} bytes")
    else:
        print(path)


if __name__ == "__main__":
    main()
