"""Package official macOS DMGs with a Clash Verge 2.5.2 native restore ZIP.

Uses the already published Windows snapshot as the single configuration source.
Upstream src-tauri/src/core/backup.rs and feat/backup.rs define a ZIP containing
config.yaml, verge.yaml, profiles.yaml, dns_config.yaml and profiles/* at root;
the app imports and restores it through Settings. No app bundle is modified,
preserving Apple's signature. macOS itself must be used for runtime validation.
"""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import time
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import yaml

from backend.core.resources.admin_downloads import publish_download_bundle, resolve_download_file
from scripts.package_clash_verge import APP_ID

VERSION = "2.5.2"
# SHA-256 published on the upstream v2.5.2 release's expanded_assets page.
ASSETS = {
    "aarch64": ("Apple 芯片（M 系列）", "94d29405980b5d1d3419dd1de485db3a234d35cef058f79dcce595e01b697219"),
    "x64": ("Intel 芯片", "c9fcec27d3e4b4fffe31f314369aaa4017d80c1293c8b1cb65d85de223e9cb6c"),
}
BACKUP_NAME = "macos-backup-codeyun-2.5.2.zip"


def build_macos_backup(windows_archive: Path) -> bytes:
    """Adapt the immutable snapshot to native backup format, without host history.

    Keep subscriptions/rules/selected nodes byte-identical. Strip Windows IPC and
    device bindings; the macOS app regenerates its Unix socket and runtime YAML.
    Disable machine registration and TUN until configured on the target Mac.
    """
    home = f"Clash-Verge/.config/{APP_ID}/"
    entries = {}
    with ZipFile(windows_archive) as source:
        for name in ("profiles.yaml", "config.yaml", "verge.yaml"):
            entries[name] = source.read(home + name)
        profiles = yaml.safe_load(entries["profiles.yaml"])
        if profiles.get("current") not in {item.get("uid") for item in profiles["items"]}:
            raise ValueError("Missing current subscription")
        for item in profiles["items"]:
            name = item.get("file")
            if not name:
                continue
            if PurePosixPath(name).name != name or "\\" in name:
                raise ValueError("Profile reference must be a filename")
            entries["profiles/" + name] = source.read(home + "profiles/" + name)
        if home + "dns_config.yaml" in source.namelist():
            entries["dns_config.yaml"] = source.read(home + "dns_config.yaml")
    core = yaml.safe_load(entries["config.yaml"])
    for name in ("external-controller-pipe", "external-controller-unix", "interface-name", "routing-mark"):
        core.pop(name, None)
    tun = core.setdefault("tun", {})
    for name in ("device", "interface-name", "windows-driver"):
        tun.pop(name, None)
    tun["enable"] = False
    verge = yaml.safe_load(entries["verge.yaml"])
    if verge.get("startup_script"):
        raise ValueError("Custom startup script requires a portability review")
    for key in ("webdav_url", "webdav_username", "webdav_password", "hotkeys"):
        verge.pop(key, None)
    verge.update(enable_auto_launch=False, enable_tun_mode=False, enable_global_hotkey=False)
    entries["config.yaml"] = yaml.safe_dump(core, allow_unicode=True, sort_keys=False).encode("utf-8")
    entries["verge.yaml"] = yaml.safe_dump(verge, allow_unicode=True, sort_keys=False).encode("utf-8")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("profiles/", b"")
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def download_official_dmg(architecture: str, directory: Path) -> Path:
    """Download an unmodified upstream asset, accepting it only after SHA-256 verification."""
    _, expected = ASSETS[architecture]
    filename = f"Clash.Verge_{VERSION}_{architecture}.dmg"
    target = directory / filename
    directory.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual == expected:
            return target
        raise ValueError("Existing DMG failed checksum")
    url = f"https://github.com/clash-verge-rev/clash-verge-rev/releases/download/v{VERSION}/{filename}"
    partial = target.with_suffix(".dmg.partial")
    for attempt in range(3):
        try:
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=45) as response, partial.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    stream.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("Official DMG failed checksum")
            partial.rename(target)
            return target
        except (OSError, urllib.error.URLError):
            if attempt == 2:
                raise
            time.sleep(2)
    raise RuntimeError("Download incomplete")


def build_macos_package(dmg: Path, backup: bytes, output: Path, *, architecture: str) -> Path:
    chip, _ = ASSETS[architecture]
    guide = f"""Clash Verge Rev {VERSION} · macOS · {chip}

一、安装
打开包内 DMG，将 Clash Verge 拖入“应用程序”，然后打开应用。
本包为 {chip} 版本；苹果菜单 → 关于本机可查看芯片类型。
系统要求 macOS 12 或以上；首次启动可能提示安装服务或授权，请按应用提示操作。

二、迁移配置（使用应用自带的备份恢复）
1. 若此 Mac 已有配置，先在 Clash Verge 的“设置 → 备份”中创建并导出备份。
2. 在“设置 → 备份”的本地备份区域，选择导入本包内的 {BACKUP_NAME}。
3. 在列表中找到导入的备份，执行恢复。注意：导入只是加入列表，仍需点击恢复。
4. 按应用提示重启，检查当前订阅、规则、代理模式和节点选择；确认系统代理已开启。

此配置备份与 CodeYun 上的 Windows 便携包来自同一份快照。
已保留订阅、规则、脚本及已保存的节点选择；未迁移日志、旧备份和临时缓存。
已移除 Windows 专用内核通信与网卡设置；开机启动、全局快捷键及 TUN 默认关闭。
如需上述功能，请在此 Mac 内重新开启或授权。此包不会同步原电脑之后的配置变化。
配置包含订阅链接和节点凭据，接收者共用订阅额度；仅向指定人员分发。
本包在 Windows 上生成，已按上游恢复格式检查，尚未在真实 Mac 上验证安装与联网。

官方程序未做修改，SHA-256：{ASSETS[architecture][1]}
程序与源码：https://github.com/clash-verge-rev/clash-verge-rev/releases/tag/v{VERSION}
备份格式：https://github.com/clash-verge-rev/clash-verge-rev/blob/v{VERSION}/src-tauri/src/core/backup.rs
"""
    with ZipFile(output, "x", compression=ZIP_STORED) as archive:
        archive.write(dmg, dmg.name)
        archive.writestr(BACKUP_NAME, backup)
        archive.writestr("使用说明.txt", guide.encode("utf-8"))
    return output


def publish_macos_package(output: Path, *, bundle_id: str, architecture: str) -> dict:
    """Publish a prepared package with its platform and restoration instructions."""
    chip, _ = ASSETS[architecture]
    return publish_download_bundle(
        bundle_id, title=f"Clash Verge Rev · Mac {chip}", files=[output], category="software",
        version=VERSION, platform=f"macOS 12+ · {chip}",
        description="内含官方 DMG、配置备份和使用说明。先安装应用，再在“设置 → 备份”中导入并恢复配置。\n"
                    "订阅、规则和已保存的节点选择与 Windows 包为同一快照；含订阅凭据，共用订阅额度。\n"
                    "恢复会覆盖同名配置，已有配置请先备份。开机启动、TUN、全局快捷键需在 Mac 重新设置。\n"
                    "安装包已核对官方 SHA-256，配置已按恢复格式检查；尚未在真实 Mac 上实测。")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-bundle-id", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--architecture", choices=list(ASSETS), required=True)
    parser.add_argument("--publish-id")
    args = parser.parse_args()
    source = resolve_download_file(args.windows_bundle_id, f"Clash-Verge-{VERSION}-Windows-x64-configured.zip")
    backup = build_macos_backup(source)
    dmg = download_official_dmg(args.architecture, args.work_dir)
    output = args.work_dir / f"Clash-Verge-{VERSION}-macOS-{args.architecture}-with-config.zip"
    build_macos_package(dmg, backup, output, architecture=args.architecture)
    if args.publish_id:
        result = publish_macos_package(output, bundle_id=args.publish_id, architecture=args.architecture)
        print(f"Published {result['id']}: {result['files'][0]['bytes']} bytes")
    else:
        print(output)


if __name__ == "__main__":
    main()
