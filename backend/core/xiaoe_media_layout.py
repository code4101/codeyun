from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


YEAR_PATTERN = re.compile(r"^20\d{2}$")


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
