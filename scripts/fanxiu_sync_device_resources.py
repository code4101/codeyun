from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.fanxiu.catalog.resources import resolve_fanxiu_resource_root


DEFAULT_ADB = Path(r"D:\TapTap\Support\android_emulator\engine\nx_device\12.0\shell\adb.exe")
DEFAULT_PACKAGE = "com.frxxcrjpwssc3.ggws"
DEFAULT_PARTS = ("filelist.csv", "filelistVersion", "lscripts", "atlasnew")
SYNC_PARTS = (*DEFAULT_PARTS, "ui", "Audio")
TARGETED_UIEFFECT_PATTERNS = (
    "*icon_0067*",
    "*icon_0052*",
)


def _run(args: list[str]) -> str:
    result = subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return result.stdout


def _adb_shell(adb: Path, command: str) -> str:
    return _run([str(adb), "shell", command])


def _adb_pull(adb: Path, remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(adb), "pull", remote, str(local)], check=True)


def _has_non_ascii_path_part(path: Path) -> bool:
    return any(ord(char) > 127 for char in str(path))


def _adb_pull_directory(adb: Path, remote: str, local: Path) -> None:
    local.mkdir(parents=True, exist_ok=True)
    if not _has_non_ascii_path_part(local):
        subprocess.run([str(adb), "pull", remote, str(local.parent)], check=True)
        pulled = local.parent / Path(remote).name
        if pulled != local and pulled.exists():
            if local.exists():
                shutil.rmtree(local)
            shutil.move(str(pulled), str(local))
        return

    temp_root = Path(tempfile.gettempdir()) / "codeyun" / "fanxiu_adb_pull"
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_dest = temp_root / Path(remote).name
    if temp_dest.exists():
        shutil.rmtree(temp_dest)
    subprocess.run([str(adb), "pull", remote, str(temp_root)], check=True)
    if not temp_dest.is_dir():
        raise FileNotFoundError(f"adb pull did not create expected directory: {temp_dest}")
    for child in temp_dest.iterdir():
        target = local / child.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(child), str(target))


def _split_remote_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def sync_path(adb: Path, remote_root: str, resource_root: Path, part: str, *, force: bool = False) -> int:
    remote = f"{remote_root}/{part}"
    local = resource_root / part
    if part.endswith(".csv") or part == "filelistVersion":
        _adb_pull(adb, remote, local)
        return 1
    if part in {"lscripts", "ui", "Audio"}:
        if local.exists() and force:
            if local.is_dir():
                shutil.rmtree(local)
            else:
                local.unlink()
        _adb_pull_directory(adb, remote, local)
        return len(list(local.rglob("*")))

    listing = _split_remote_lines(_adb_shell(adb, f"find {remote} -maxdepth 1 -type f 2>/dev/null"))
    count = 0
    for remote_file in listing:
        local_file = local / Path(remote_file).name
        if local_file.is_file() and not force:
            count += 1
            continue
        _adb_pull(adb, remote_file, local)
        count += 1
    return count


def sync_targeted_uieffects(adb: Path, remote_root: str, resource_root: Path, *, force: bool = False) -> int:
    local = resource_root / "uieffect"
    local.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    count = 0
    for pattern in TARGETED_UIEFFECT_PATTERNS:
        listing = _split_remote_lines(_adb_shell(adb, f"find {remote_root}/uieffect -maxdepth 1 -type f -name '{pattern}' 2>/dev/null"))
        for remote_file in listing:
            if remote_file in seen:
                continue
            seen.add(remote_file)
            local_file = local / Path(remote_file).name
            if local_file.is_file() and not force:
                count += 1
                continue
            _adb_pull(adb, remote_file, local)
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Fanxiu hot-update resources from the current Android device cache.")
    parser.add_argument("--adb", default=str(DEFAULT_ADB), help="Path to adb.exe")
    parser.add_argument("--package", default=DEFAULT_PACKAGE, help="Android package name")
    parser.add_argument("--resource-root", default=None, help="Local frxx_game_files root")
    parser.add_argument("--part", action="append", choices=[*SYNC_PARTS, "uieffect-targeted"], help="Resource part to sync; repeatable")
    parser.add_argument("--force", action="store_true", help="Overwrite existing local files")
    args = parser.parse_args()

    adb = Path(args.adb).expanduser().resolve()
    if not adb.is_file():
        raise SystemExit(f"adb not found: {adb}")

    resource_root = resolve_fanxiu_resource_root(args.resource_root)
    resource_root.mkdir(parents=True, exist_ok=True)
    remote_root = f"/sdcard/Android/data/{args.package}/files"
    parts = tuple(args.part or DEFAULT_PARTS)

    print(f"resource_root={resource_root}")
    print(f"remote_root={remote_root}")
    for part in parts:
        if part == "uieffect-targeted":
            count = sync_targeted_uieffects(adb, remote_root, resource_root, force=args.force)
        else:
            count = sync_path(adb, remote_root, resource_root, part, force=args.force)
        print(f"{part}: {count}")


if __name__ == "__main__":
    main()
