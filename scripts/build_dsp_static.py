from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


DEFAULT_DSP_DIR = r"D:\home\chenkunze\slns+\dsp-calc"
TARGET_DIR_NAME = "dsp-calc"
SYNC_METADATA_NAME = f"{TARGET_DIR_NAME}.json"
METADATA_VERSION = 1

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CODEYUN_PUBLIC_DIR = PROJECT_ROOT / "frontend" / "public"
TARGET_DIR = CODEYUN_PUBLIC_DIR / TARGET_DIR_NAME
SYNC_STATE_DIR = PROJECT_ROOT / "frontend" / ".codeyun-state"
SYNC_METADATA_PATH = SYNC_STATE_DIR / SYNC_METADATA_NAME

SOURCE_DIR_NAMES = ("src", "public", "css", "data", "icon")
ROOT_FILE_NAMES = {
    "index.html",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}
ROOT_FILE_PREFIXES = (
    ".env",
    "vite.config.",
    "tsconfig",
    "postcss.config.",
    "tailwind.config.",
    "babel.config.",
)
PACKAGE_MANIFEST_FILES = (
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
)
IGNORED_PATH_PARTS = {".git", "node_modules", "dist", "__pycache__"}


@dataclass(frozen=True)
class SourceSnapshot:
    source_dir: Path
    source_hash: str
    package_manifest_hash: str
    files_scanned: int
    git_head: str | None
    git_dirty: bool | None


def resolve_source_dir() -> Path:
    return Path(os.getenv("DSP_SOURCE_DIR", DEFAULT_DSP_DIR)).expanduser()


def run_command(command: list[str], cwd: Path) -> None:
    resolved_command = command[:]
    if os.name == "nt" and resolved_command and resolved_command[0] == "npm":
        resolved_command[0] = "npm.cmd"

    print(f"正在执行: {' '.join(command)} (目录: {cwd})")
    try:
        subprocess.run(resolved_command, cwd=cwd, check=True)
    except FileNotFoundError:
        print(f"错误: 未找到命令 {command[0]}，请确认已安装 Node.js/npm。")
        raise SystemExit(1) from None
    except subprocess.CalledProcessError as exc:
        print(f"命令执行失败: {exc}")
        raise SystemExit(exc.returncode) from exc


def is_relevant_root_file(path: Path) -> bool:
    if not path.is_file():
        return False

    if path.name in ROOT_FILE_NAMES:
        return True

    return any(path.name.startswith(prefix) for prefix in ROOT_FILE_PREFIXES)


def iter_relevant_source_files(source_dir: Path) -> list[Path]:
    files: set[Path] = set()

    for child in source_dir.iterdir():
        if is_relevant_root_file(child):
            files.add(child)

    for directory_name in SOURCE_DIR_NAMES:
        directory = source_dir / directory_name
        if not directory.exists():
            continue

        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORED_PATH_PARTS for part in path.parts):
                continue
            files.add(path)

    return sorted(files, key=lambda path: path.relative_to(source_dir).as_posix())


def hash_files(base_dir: Path, files: list[Path], *, prefix: str) -> str:
    digest = hashlib.sha256()
    digest.update(prefix.encode("utf-8"))
    digest.update(b"\0")

    for path in files:
        relative_path = path.relative_to(base_dir).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")

    return digest.hexdigest()


def get_git_value(source_dir: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_dir), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None
    return result.stdout.strip()


def collect_source_snapshot(source_dir: Path, target_dir_name: str = TARGET_DIR_NAME) -> SourceSnapshot:
    files = iter_relevant_source_files(source_dir)
    package_files = [path for path in files if path.name in PACKAGE_MANIFEST_FILES]

    return SourceSnapshot(
        source_dir=source_dir,
        source_hash=hash_files(source_dir, files, prefix=f"base=/{target_dir_name}/"),
        package_manifest_hash=hash_files(source_dir, package_files, prefix="package-manifest"),
        files_scanned=len(files),
        git_head=get_git_value(source_dir, "rev-parse", "HEAD"),
        git_dirty=bool(get_git_value(source_dir, "status", "--short")) if (source_dir / ".git").exists() else None,
    )


def read_sync_metadata(metadata_path: Path) -> dict[str, object] | None:
    if not metadata_path.exists():
        return None

    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_sync_metadata(snapshot: SourceSnapshot) -> dict[str, object]:
    return {
        "metadata_version": METADATA_VERSION,
        "target_dir_name": TARGET_DIR_NAME,
        "source_dir": str(snapshot.source_dir),
        "source_hash": snapshot.source_hash,
        "package_manifest_hash": snapshot.package_manifest_hash,
        "files_scanned": snapshot.files_scanned,
        "git_head": snapshot.git_head,
        "git_dirty": snapshot.git_dirty,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "build_base": f"/{TARGET_DIR_NAME}/",
    }


def write_sync_metadata(metadata_path: Path, metadata: dict[str, object]) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_sync_reason(
    target_dir: Path,
    snapshot: SourceSnapshot,
    metadata: dict[str, object] | None,
    *,
    force: bool,
) -> tuple[bool, str]:
    if force:
        return True, "已指定 --force，强制重新构建。"
    if not target_dir.exists():
        return True, f"目标目录不存在: {target_dir}"
    if metadata is None:
        return True, f"缺少同步元数据: {SYNC_METADATA_PATH}"
    if metadata.get("metadata_version") != METADATA_VERSION:
        return True, "同步元数据版本不匹配。"
    if metadata.get("target_dir_name") != TARGET_DIR_NAME:
        return True, "目标目录标识不匹配。"
    if metadata.get("source_hash") != snapshot.source_hash:
        return True, "检测到 DSP 源码内容变化。"
    return False, "DSP 静态资源已是最新，无需更新。"


def ensure_dependencies(source_dir: Path, metadata: dict[str, object] | None, snapshot: SourceSnapshot) -> None:
    node_modules_path = source_dir / "node_modules"
    if not node_modules_path.exists():
        print("检测到未安装依赖，正在执行 npm install...")
        run_command(["npm", "install"], cwd=source_dir)
        return

    if metadata is None:
        print("首次写入同步元数据，执行一次 npm install 以校准依赖...")
        run_command(["npm", "install"], cwd=source_dir)
        return

    if metadata.get("package_manifest_hash") != snapshot.package_manifest_hash:
        print("检测到依赖清单变化，正在执行 npm install...")
        run_command(["npm", "install"], cwd=source_dir)
        return

    print("依赖未变化，跳过 npm install。")


def deploy_dist_dir(dist_dir: Path, target_dir: Path) -> None:
    parent_dir = target_dir.parent
    parent_dir.mkdir(parents=True, exist_ok=True)

    staging_root = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}-staging-", dir=parent_dir))
    staging_target = staging_root / target_dir.name

    try:
        shutil.copytree(dist_dir, staging_target)

        if target_dir.exists():
            remove_tree(target_dir)

        shutil.move(str(staging_target), str(target_dir))
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def remove_tree(path: Path) -> None:
    def onerror(function, failed_path, exc_info):  # type: ignore[no-untyped-def]
        failed = Path(failed_path)
        failed.chmod(stat.S_IWRITE)
        function(failed_path)

    shutil.rmtree(path, onerror=onerror)


def sync_dsp_static(*, force: bool = False) -> int:
    source_dir = resolve_source_dir()
    if not source_dir.exists():
        print(f"错误: DSP 源码目录不存在: {source_dir}")
        print("请在 .env 文件中配置 DSP_SOURCE_DIR 或检查路径。")
        return 1

    snapshot = collect_source_snapshot(source_dir)
    metadata = read_sync_metadata(SYNC_METADATA_PATH)
    should_sync, reason = get_sync_reason(TARGET_DIR, snapshot, metadata, force=force)

    print("=== 开始同步戴森球计划计算器静态资源 ===")
    print(f"DSP 源目录: {source_dir}")
    if snapshot.git_head:
        dirty_suffix = " (dirty)" if snapshot.git_dirty else ""
        print(f"Git HEAD: {snapshot.git_head}{dirty_suffix}")
    print(f"已扫描文件数: {snapshot.files_scanned}")
    print(reason)

    if not should_sync:
        print(f"访问路径: /{TARGET_DIR_NAME}/index.html")
        return 0

    ensure_dependencies(source_dir, metadata, snapshot)

    print(f"正在构建项目 (Base URL: /{TARGET_DIR_NAME}/)...")
    run_command(["npm", "run", "build", "--", f"--base=/{TARGET_DIR_NAME}/"], cwd=source_dir)

    dist_dir = source_dir / "dist"
    if not dist_dir.exists():
        print(f"错误: 构建目录 {dist_dir} 不存在，请检查构建过程是否出错。")
        return 1

    deploy_dist_dir(dist_dir, TARGET_DIR)
    write_sync_metadata(SYNC_METADATA_PATH, build_sync_metadata(snapshot))

    print("=== 构建与部署完成 ===")
    print(f"访问路径: /{TARGET_DIR_NAME}/index.html")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建并同步 DSP 静态资源到 CodeYun 前端 public 目录。")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已部署元数据，强制重新构建并同步。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return sync_dsp_static(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
