"""把历史 Pixi 目录族无损合并回唯一的 Pixiv 目录族。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from filelock import FileLock
from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.filesystem import DeviceFileScanRequest, scan_device_file_records
from backend.core.devices.device import get_device_id
from backend.core.devices.files import reconcile_tiered_media_weight_aliases
from backend.db import engine
from backend.models import DeviceFile
from backend.plugins.modules.media_sync.models import MediaSyncSourceItem
from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lock_path
from backend.plugins.modules.media_sync.sources import count_local_media_files


TIER_MAPPINGS = (
    ("1、pixi", "1、pixiv"),
    ("2、pixi", "2、pixiv"),
    ("3、pixi", "3、pixiv"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def build_move_plan(root: Path) -> dict[Path, Path]:
    plan: dict[Path, Path] = {}
    for source_name, destination_name in TIER_MAPPINGS:
        source_root = root / source_name
        destination_root = root / destination_name
        if not source_root.exists():
            continue
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            destination = destination_root / source.relative_to(source_root)
            if destination.exists():
                raise RuntimeError(f"Pixiv 合并目标已存在，拒绝覆盖: {destination}")
            plan[source] = destination
    return plan


def canonical_pixiv_path(raw_path: str) -> str | None:
    """把路径中的完整 Pixi 层级组件改成 Pixiv，不误伤 ``pixiv``。"""

    value = str(raw_path or "")
    folded = value.casefold()
    for source_name, destination_name in TIER_MAPPINGS:
        marker = f"\\{source_name}\\"
        index = folded.find(marker.casefold())
        if index >= 0:
            return f"{value[:index]}\\{destination_name}\\{value[index + len(marker):]}"
    return None


def update_database(*, plan: dict[Path, Path]) -> dict[str, int]:
    normalized_path_map = {
        str(source.resolve(strict=False)).casefold(): destination
        for source, destination in plan.items()
    }
    exact_path_map = {str(source): str(destination) for source, destination in plan.items()}
    counts = {"rating_family": 0, "source_paths": 0, "device_paths": 0}
    now = time.time()
    with Session(engine) as session:
        source_items = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.platform == "pixiv",
            )
        ).all()
        for item in source_items:
            extra = item.extra_json or {}
            if extra.get("rating_family") != "pixiv":
                item.extra_json = {**extra, "rating_family": "pixiv"}
                counts["rating_family"] += 1
            old_path = str(item.absolute_path or "").strip()
            destination = (
                normalized_path_map.get(str(Path(old_path).resolve(strict=False)).casefold())
                if old_path
                else None
            )
            if destination is None and old_path:
                canonical_path = canonical_pixiv_path(old_path)
                destination = Path(canonical_path) if canonical_path else None
            if destination is not None:
                item.absolute_path = str(destination)
                counts["source_paths"] += 1
            if extra.get("rating_family") != "pixiv" or destination is not None:
                item.updated_at = now
                session.add(item)

        device_rows = session.exec(select(DeviceFile)).all()
        for record in device_rows:
            absolute_destination = exact_path_map.get(str(record.absolute_path or "")) or canonical_pixiv_path(
                str(record.absolute_path or "")
            )
            last_known_destination = exact_path_map.get(str(record.last_known_path or "")) or canonical_pixiv_path(
                str(record.last_known_path or "")
            )
            if absolute_destination is None and last_known_destination is None:
                continue
            if absolute_destination is not None:
                record.absolute_path = str(absolute_destination)
            if last_known_destination is not None:
                record.last_known_path = str(last_known_destination)
            record.updated_at = now
            record.last_seen_at = now
            session.add(record)
            counts["device_paths"] += 1
        session.commit()
    return counts


def prune_empty_source_directories(root: Path) -> list[str]:
    removed: list[str] = []
    for source_name, _destination_name in TIER_MAPPINGS:
        source_root = root / source_name
        if not source_root.exists():
            continue
        directories = sorted(
            (path for path in source_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in (*directories, source_root):
            try:
                directory.rmdir()
            except OSError:
                continue
            removed.append(str(directory))
    return removed


def rebuild_current_device_indexes(root: Path) -> dict[str, object]:
    device_id = get_device_id()
    scans: dict[str, object] = {}
    for _source_name, destination_name in TIER_MAPPINGS:
        destination_root = root / destination_name
        with Session(engine) as session:
            result = scan_device_file_records(
                DeviceFileScanRequest(
                    absolute_path=str(destination_root),
                    recursive=True,
                    hash_mode="never",
                    mark_missing_as_dangling=True,
                ),
                session,
                device_id=device_id,
            )
        scans[destination_name] = {
            key: value
            for key, value in result.items()
            if key in {"ok", "processed_count", "created_count", "rebound_count", "updated_count", "dangling_count"}
        }
    with Session(engine) as session:
        weights = reconcile_tiered_media_weight_aliases(
            session,
            device_id,
            root_dir=str(root),
        )
    return {"scans": scans, "weights": weights}


def main() -> int:
    args = parse_args()
    root = Path(args.root_dir).expanduser().resolve()
    plan = build_move_plan(root)
    summary: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "planned_file_moves": len(plan),
        "planned_bytes": sum(source.stat().st_size for source in plan),
        "before": {
            source_name: count_local_media_files(root / source_name)
            for source_name, _destination_name in TIER_MAPPINGS
        },
    }
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=True, indent=2))
        return 0

    moved: list[tuple[Path, Path]] = []
    with FileLock(str(pixiv_source_activity_lock_path().resolve()), timeout=0):
        try:
            for source, destination in plan.items():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                moved.append((source, destination))
            summary["database_updates"] = update_database(plan=plan)
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, source)
            raise
        summary["removed_empty_directories"] = prune_empty_source_directories(root)
        summary["device_index_rebuild"] = rebuild_current_device_indexes(root)

    summary["moved_files"] = len(moved)
    summary["after"] = {
        destination_name: count_local_media_files(root / destination_name)
        for _source_name, destination_name in TIER_MAPPINGS
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
