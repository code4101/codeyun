from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))
PROJECT_DIR = ROOT / "android" / "fanxiu-info-window"
PACKAGE = "com.codeyun.fanxiu.infowindow"
TOOLCHAIN_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "CodeYun" / "toolchains"


def _first_file(patterns: list[str]) -> Path | None:
    for pattern in patterns:
        for path in Path.home().glob(pattern):
            if path.is_file():
                return path
    return None


def resolve_java_home() -> Path:
    candidates = [
        Path(os.environ["JAVA_HOME"]) if os.environ.get("JAVA_HOME") else None,
        TOOLCHAIN_ROOT / "jdk-21",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "bin" / "javac.exe").is_file():
            return candidate
    raise RuntimeError("缺少 JDK 21；请设置 JAVA_HOME，或安装到 %LOCALAPPDATA%/CodeYun/toolchains/jdk-21")


def resolve_android_sdk() -> Path:
    candidates = [
        Path(os.environ[name])
        for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME")
        if os.environ.get(name)
    ]
    candidates.extend([TOOLCHAIN_ROOT / "android-sdk", Path.home() / "AppData" / "Local" / "Android" / "Sdk"])
    for candidate in candidates:
        if (candidate / "platforms" / "android-35" / "android.jar").is_file():
            return candidate
    raise RuntimeError("缺少 Android SDK 35；请设置 ANDROID_SDK_ROOT")


def resolve_gradle() -> Path:
    path_gradle = shutil.which("gradle")
    if path_gradle:
        return Path(path_gradle)
    cached = _first_file([".gradle/wrapper/dists/gradle-8.9-bin/*/gradle-8.9/bin/gradle.bat"])
    if cached is not None:
        return cached
    raise RuntimeError("缺少 Gradle 8.9")


def build_apk() -> Path:
    java_home = resolve_java_home()
    android_sdk = resolve_android_sdk()
    gradle = resolve_gradle()
    output_root = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu-info-window-build"
    env = dict(os.environ)
    env.update({
        "JAVA_HOME": os.fspath(java_home),
        "ANDROID_SDK_ROOT": os.fspath(android_sdk),
        "ANDROID_HOME": os.fspath(android_sdk),
        "CODEYUN_FANXIU_INFO_WINDOW_BUILD_DIR": os.fspath(output_root),
    })
    subprocess.run(
        [
            os.fspath(gradle),
            "--no-daemon",
            "--console=plain",
            f"-Pandroid.aapt2FromMavenOverride={android_sdk / 'build-tools' / '34.0.0' / 'aapt2.exe'}",
            ":app:assembleDebug",
        ],
        cwd=PROJECT_DIR,
        env=env,
        check=True,
    )
    apk = output_root / "app" / "outputs" / "apk" / "debug" / "app-debug.apk"
    if not apk.is_file():
        raise RuntimeError(f"APK 构建完成但未找到产物：{apk}")
    return apk


def adb_command() -> tuple[Path, str]:
    from backend.core.fanxiu.runtime.adb_device import fanxiu_adb_device_service

    return fanxiu_adb_device_service.adb_path(), fanxiu_adb_device_service.choose_device()


def install_apk(apk: Path) -> None:
    adb, device = adb_command()
    prefix = [os.fspath(adb), "-s", device]
    subprocess.run([*prefix, "install", "-r", os.fspath(apk)], check=True)
    subprocess.run([*prefix, "shell", "cmd", "appops", "set", PACKAGE, "SYSTEM_ALERT_WINDOW", "allow"], check=True)
    subprocess.run([
        *prefix,
        "shell",
        "am",
        "broadcast",
        "-a",
        f"{PACKAGE}.UPDATE",
        "-n",
        f"{PACKAGE}/.InfoWindowReceiver",
        "--ei",
        "scene_id",
        "-1",
        "--ef",
        "score",
        "0",
    ], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="构建并可选安装凡修信息窗 APK")
    parser.add_argument("--install", action="store_true", help="构建后安装到当前凡修 MuMu 设备")
    args = parser.parse_args()
    apk = build_apk()
    if args.install:
        install_apk(apk)
    print(apk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
