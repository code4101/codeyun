"""将旧下划线媒体目录迁移到统一的 1、/2、/3、层级。"""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from filelock import FileLock, Timeout
from sqlalchemy import inspect, text
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core.temp_paths import codeyun_temp_root
from backend.core.devices.device import get_device_id
from backend.core.devices.files import reconcile_tiered_media_weight_aliases
from backend.db import engine
from backend.plugins.modules.media_sync.runtime import (
    pixiv_source_activity_lock_path,
    video_source_activity_lock_path,
)


DIRECTORY_MAPPINGS = (
    ("pixiv", "1、pixiv"),
    ("_pixiv", "2、pixiv"),
    ("__pixiv", "3、pixiv"),
    ("pinterest", "1、pinterest"),
    ("_pinterest", "2、pinterest"),
    ("__pinterest", "3、pinterest"),
    ("video", "1、video"),
    ("__video", "3、video"),
)

PATH_COLUMNS = (
    ("private_media_sync_source_item", "absolute_path"),
    ("private_media_sync_collection_membership", "example_absolute_path"),
    ("devicefile", "absolute_path"),
    ("devicefile", "last_known_path"),
    ("devicefile", "cover_path"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--apply", action="store_true", help="实际执行；默认仅预览")
    return parser.parse_args()


def count_files(path: Path) -> int:
    return sum(1 for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def acquire_pinterest_backlog_lock(stack: ExitStack):
    lock_path = codeyun_temp_root("media-sync") / "pinterest-backlog.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = stack.enter_context(lock_path.open("a+b"))
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    return handle


def build_preview(root: Path) -> list[dict[str, object]]:
    preview = []
    for old_name, new_name in DIRECTORY_MAPPINGS:
        source = root / old_name
        destination = root / new_name
        preview.append(
            {
                "source": str(source),
                "destination": str(destination),
                "source_exists": source.exists(),
                "destination_exists": destination.exists(),
                "file_count": count_files(source),
            }
        )
    preview.append(
        {
            "source": None,
            "destination": str(root / "2、video"),
            "source_exists": False,
            "destination_exists": (root / "2、video").exists(),
            "file_count": 0,
        }
    )
    return preview


def rewrite_database_paths(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.begin() as connection:
        table_names = set(inspect(connection).get_table_names())
        for table_name, column_name in PATH_COLUMNS:
            if table_name not in table_names:
                continue
            for old_name, new_name in DIRECTORY_MAPPINGS:
                old_prefix = str(root / old_name)
                new_prefix = str(root / new_name)
                result = connection.execute(
                    text(
                        f'UPDATE "{table_name}" SET "{column_name}" = '
                        f':new_prefix || substr("{column_name}", length(:old_prefix) + 1) '
                        f'WHERE "{column_name}" = :old_prefix '
                        f'OR substr("{column_name}", 1, length(:old_prefix) + 1) = :old_children'
                    ),
                    {
                        "old_prefix": old_prefix,
                        "new_prefix": new_prefix,
                        "old_children": old_prefix + "\\",
                    },
                )
                if result.rowcount:
                    key = f"{table_name}.{column_name}"
                    counts[key] = counts.get(key, 0) + int(result.rowcount)

        # JSON 中只替换明确的旧目录段，涵盖 rejected_path 和历史运行摘要。
        json_columns = (
            ("private_media_sync_source_item", "extra_json"),
            ("private_media_sync_profile", "last_run_summary"),
        )
        for table_name, column_name in json_columns:
            if table_name not in table_names:
                continue
            for old_name, new_name in DIRECTORY_MAPPINGS:
                old_prefix = json.dumps(str(root / old_name), ensure_ascii=False)[1:-1]
                new_prefix = json.dumps(str(root / new_name), ensure_ascii=False)[1:-1]
                result = connection.execute(
                    text(
                        f'UPDATE "{table_name}" SET "{column_name}" = '
                        f'replace("{column_name}", :old_prefix, :new_prefix) '
                        f'WHERE "{column_name}" LIKE :needle'
                    ),
                    {
                        "old_prefix": old_prefix,
                        "new_prefix": new_prefix,
                        "needle": f"%{old_prefix}%",
                    },
                )
                if result.rowcount:
                    key = f"{table_name}.{column_name}"
                    counts[key] = counts.get(key, 0) + int(result.rowcount)
    return counts


def main() -> int:
    args = parse_args()
    root = Path(args.root_dir).expanduser().resolve()
    preview = build_preview(root)
    conflicts = [row for row in preview if row["source_exists"] and row["destination_exists"]]
    if conflicts:
        raise RuntimeError(f"目标目录已存在，拒绝合并: {conflicts}")
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "directories": preview}, ensure_ascii=False, indent=2))
        return 0

    renamed: list[tuple[Path, Path]] = []
    try:
        with ExitStack() as stack:
            stack.enter_context(FileLock(str(video_source_activity_lock_path().resolve()), timeout=0))
            stack.enter_context(FileLock(str(pixiv_source_activity_lock_path().resolve()), timeout=0))
            acquire_pinterest_backlog_lock(stack)
            for old_name, new_name in DIRECTORY_MAPPINGS:
                source = root / old_name
                destination = root / new_name
                if not source.exists():
                    continue
                os.replace(source, destination)
                renamed.append((source, destination))
            (root / "2、video").mkdir(parents=True, exist_ok=True)
            database_updates = rewrite_database_paths(root)
            with Session(engine) as session:
                weight_reconcile = reconcile_tiered_media_weight_aliases(
                    session,
                    get_device_id(),
                    root_dir=str(root),
                )
    except (Timeout, OSError) as exc:
        for source, destination in reversed(renamed):
            if destination.exists() and not source.exists():
                os.replace(destination, source)
        raise RuntimeError(f"媒体同步仍在运行或迁移失败，目录已回滚: {exc}") from exc
    except Exception:
        for source, destination in reversed(renamed):
            if destination.exists() and not source.exists():
                os.replace(destination, source)
        raise

    print(
        json.dumps(
            {
                "mode": "applied",
                "renamed": [{"source": str(source), "destination": str(destination)} for source, destination in renamed],
                "created": str(root / "2、video"),
                "database_updates": database_updates,
                "weight_reconcile": weight_reconcile,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
