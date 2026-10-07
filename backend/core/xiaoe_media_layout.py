from __future__ import annotations

import json
import re
import hashlib
import shutil
import msvcrt
from contextlib import ExitStack
from pathlib import Path
from typing import Any


YEAR_PATTERN = re.compile(r"^20\d{2}$")


def consolidate_video_archives(source_dir: Path, target_dir: Path) -> dict[str, int]:
    """合并分散的视频增量及其索引，保留源文件，冲突时停止。

    仅复制视频子目录，逐个核验内容哈希；不迁移全量游标或覆盖目标业务状态。
    """
    source_dir, target_dir = Path(source_dir).resolve(), Path(target_dir).resolve()
    if source_dir == target_dir or source_dir.is_relative_to(target_dir) or target_dir.is_relative_to(source_dir):
        raise ValueError("归档源和目标必须是独立目录")
    with ExitStack() as stack:
        for directory in sorted((source_dir, target_dir)):
            helper_dir = directory / "_下载辅助"
            helper_dir.mkdir(parents=True, exist_ok=True)
            lock = stack.enter_context((helper_dir / "daemon.lock").open("a+b"))
            if lock.seek(0, 2) == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError(f"该归档目录已有下载任务：{directory}") from exc
        return _consolidate_video_archive_files(source_dir, target_dir)


def _consolidate_video_archive_files(source_dir: Path, target_dir: Path) -> dict[str, int]:
    copied, existing = 0, 0

    def digest(path: Path) -> str:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    for source in sorted((source_dir / "视频").rglob("*.mp4")):
        if source.name.endswith(".downloading.mp4"):
            continue
        source.resolve().relative_to(source_dir)
        target = target_dir / source.relative_to(source_dir)
        target.resolve().relative_to(target_dir)
        if target.exists():
            if digest(source) != digest(target):
                raise FileExistsError(f"归档文件内容冲突：{target}")
            existing += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".merge.tmp")
        shutil.copy2(source, temporary)
        if digest(source) != digest(temporary):
            raise RuntimeError(f"归档复制校验失败：{target}")
        temporary.replace(target)
        copied += 1
    source_index = source_dir / "_下载辅助" / "catalog-index.json"
    target_index = target_dir / "_下载辅助" / "catalog-index.json"
    entries = json.loads(source_index.read_text(encoding="utf-8")) if source_index.exists() else {}
    current = json.loads(target_index.read_text(encoding="utf-8")) if target_index.exists() else {}
    merged = current.setdefault("items", {})
    for key, value in (entries.get("items") or {}).items():
        result = value.get("result") or {}
        if result.get("path"):
            old_path = Path(result["path"])
            if old_path.is_relative_to(source_dir):
                result["path"] = str(target_dir / old_path.relative_to(source_dir))
        previous = merged.get(key)
        if not previous or value.get("processed_at", "") > previous.get("processed_at", ""):
            merged[key] = value
    target_index.parent.mkdir(parents=True, exist_ok=True)
    current["version"] = 1
    temporary_index = target_index.with_suffix(".json.tmp")
    temporary_index.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_index.replace(target_index)
    return {"copied": copied, "existing": existing, "indexed": len(merged)}


def _rewrite_legacy_paths(value: Any, output_dir: Path) -> Any:
    if isinstance(value, str):
        for year in range(2000, 2100):
            old = str(output_dir / str(year))
            if value.startswith(old + "\\") or value.startswith(old + "/"):
                return str(output_dir / "视频" / str(year)) + value[len(old) :]
        return value
    if isinstance(value, list):
        return [_rewrite_legacy_paths(item, output_dir) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_legacy_paths(item, output_dir) for key, item in value.items()}
    return value


def migrate_legacy_video_layout(output_dir: Path) -> dict[str, int]:
    output_dir = output_dir.resolve()
    video_root = output_dir / "视频"
    moved = 0
    rewritten = 0
    removed_dirs = 0
    for source_dir in sorted(output_dir.iterdir() if output_dir.exists() else []):
        if not source_dir.is_dir() or not YEAR_PATTERN.fullmatch(source_dir.name):
            continue
        target_dir = video_root / source_dir.name
        target_dir.mkdir(parents=True, exist_ok=True)
        for source in source_dir.iterdir():
            if not source.is_file():
                continue
            target = target_dir / source.name
            if target.exists():
                if target.stat().st_size != source.stat().st_size:
                    raise FileExistsError(f"目标文件已存在且大小不同：{target}")
                continue
            source.replace(target)
            moved += 1
        if not any(source_dir.iterdir()):
            source_dir.rmdir()
            removed_dirs += 1

    helper_dir = output_dir / "_下载辅助"
    if helper_dir.exists():
        for path in helper_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            updated = _rewrite_legacy_paths(payload, output_dir)
            if updated == payload:
                continue
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp_path.replace(path)
            rewritten += 1
    return {
        "moved_files": moved,
        "removed_legacy_year_dirs": removed_dirs,
        "rewritten_state_files": rewritten,
    }
