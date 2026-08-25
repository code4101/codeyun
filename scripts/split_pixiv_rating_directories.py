"""旧 Pixiv 分类资料读取工具；目录拆分能力已停用。

Pixi/Pixiv 的本地存储边界不可靠，正式迁移入口是
``scripts/merge_pixi_into_pixiv.py``。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from filelock import FileLock
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.db import engine
from backend.api.filesystem import DeviceFileScanRequest, scan_device_file_records
from backend.core.devices.device import get_device_id
from backend.core.devices.files import reconcile_tiered_media_weight_aliases
from backend.core.temp_paths import codeyun_temp_root
from backend.models import DeviceFile
from backend.plugins.modules.media_sync.models import MediaSyncSourceItem
from backend.plugins.modules.media_sync.runtime import pixiv_source_activity_lock_path
from backend.plugins.modules.media_sync.sources import normalize_pixiv_tags, pixiv_rating_family


TIER_MAPPINGS = (
    ("1、pixiv", "1、pixi"),
    ("2、pixiv", "2、pixi"),
    ("3、pixiv", "3、pixi"),
)
ARTWORK_ID_RE = re.compile(r"(?<!\d)(\d{6,})(?!\d)")
PIXIV_VISUAL_SENSITIVE_ARTWORK_IDS = frozenset(
    {
        "104243719",
        "107746832",
        "108048446",
        "116953763",
        "117081816",
        "117311479",
        "117419209",
        "134672622",
        "139930474",
        "142710908",
        "66979373",
        "67244132",
    }
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-dir", default=r"E:\data\m2510mn")
    parser.add_argument("--user-id", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def pixiv_path_hints(root: Path, path: Path) -> tuple[str, ...]:
    """返回媒体相对根目录的全部路径提示，包含作品父目录名。"""

    try:
        relative_path = path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        relative_path = path
    hints = [*relative_path.parts[:-1], relative_path.stem]
    return normalize_pixiv_tags(hints)


def load_classifications(root: Path) -> dict[str, dict[str, object]]:
    classifications: dict[str, dict[str, object]] = {}
    state_root = root / "1、pixiv" / "_state"
    for db_path in state_root.glob("*.sqlite3"):
        connection = sqlite3.connect(db_path)
        try:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(artworks)")}
            tags_expression = "tags_json" if "tags_json" in columns else "NULL"
            for artwork_id, x_restrict, tags_json, title, alt in connection.execute(
                f"SELECT artwork_id, x_restrict, {tags_expression}, title, alt FROM artworks"
            ):
                key = str(artwork_id)
                current = classifications.setdefault(
                    key,
                    {"x_restrict": 0, "tags": [], "title": "", "alt": "", "source_kinds": [], "collection_urls": []},
                )
                current["x_restrict"] = max(int(x_restrict or 0), int(current["x_restrict"] or 0))
                current["tags"] = list(
                    dict.fromkeys((*normalize_pixiv_tags(current.get("tags")), *normalize_pixiv_tags(tags_json)))
                )
                current["title"] = str(title or current.get("title") or "")
                current["alt"] = str(alt or current.get("alt") or "")
        finally:
            connection.close()
    cache_path = codeyun_temp_root("pixiv-rating") / "x-restrict.json"
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        for artwork_id, x_restrict in cached.items():
            current = classifications.setdefault(
                str(artwork_id),
                {"x_restrict": 0, "tags": [], "title": "", "alt": "", "source_kinds": [], "collection_urls": []},
            )
            current["x_restrict"] = max(int(x_restrict or 0), int(current["x_restrict"] or 0))

    with Session(engine) as session:
        source_items = session.exec(
            select(MediaSyncSourceItem).where(MediaSyncSourceItem.platform == "pixiv")
        ).all()
        for item in source_items:
            key = str(item.remote_id or "").strip()
            if not key:
                continue
            current = classifications.setdefault(
                key,
                {"x_restrict": 0, "tags": [], "title": "", "alt": "", "source_kinds": [], "collection_urls": []},
            )
            extra = item.extra_json or {}
            current["x_restrict"] = max(int(extra.get("x_restrict") or 0), int(current["x_restrict"] or 0))
            current["tags"] = list(
                dict.fromkeys((*normalize_pixiv_tags(current.get("tags")), *normalize_pixiv_tags(extra.get("tags"))))
            )
            current["source_kinds"] = list(
                dict.fromkeys((*normalize_pixiv_tags(current.get("source_kinds")), str(item.source_kind or "")))
            )
            current["collection_urls"] = list(
                dict.fromkeys((*normalize_pixiv_tags(current.get("collection_urls")), str(item.collection_url or "")))
            )
            path_hints = pixiv_path_hints(root, Path(str(item.absolute_path or "")))
            current["path_hints"] = list(
                dict.fromkeys((*normalize_pixiv_tags(current.get("path_hints")), *path_hints))
            )
    return classifications


def classification_family(classification: dict[str, object]) -> str:
    return pixiv_rating_family(
        classification.get("x_restrict"),
        classification.get("tags"),
        title=" ".join(
            (
                str(classification.get("title") or ""),
                *normalize_pixiv_tags(classification.get("path_hints")),
            )
        ),
        alt=classification.get("alt"),
        source_kind=" ".join(normalize_pixiv_tags(classification.get("source_kinds"))),
        collection_url=" ".join(normalize_pixiv_tags(classification.get("collection_urls"))),
    )


def infer_artwork_id(path: Path, classifications: dict[str, dict[str, object]]) -> str | None:
    for part in reversed(path.parts):
        for match in ARTWORK_ID_RE.finditer(part):
            if match.group(1) in classifications:
                return match.group(1)
    return None


def build_move_plan(
    root: Path,
    classifications: dict[str, dict[str, object]],
) -> dict[Path, tuple[Path, dict[str, object]]]:
    plan: dict[Path, tuple[Path, dict[str, object]]] = {}
    source_classifications: dict[str, dict[str, object]] = {}
    with Session(engine) as session:
        rows = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.platform == "pixiv",
                MediaSyncSourceItem.absolute_path.is_not(None),
            )
        ).all()
        for row in rows:
            classification = classifications.get(str(row.remote_id))
            if classification is not None:
                source_classifications[str(Path(str(row.absolute_path)).resolve(strict=False)).lower()] = classification

    for source_name, destination_name in TIER_MAPPINGS:
        source_root = root / source_name
        destination_root = root / destination_name
        if not source_root.exists():
            continue
        for source_path in source_root.rglob("*"):
            if not source_path.is_file() or "_state" in source_path.relative_to(source_root).parts:
                continue
            resolved_key = str(source_path.resolve(strict=False)).lower()
            artwork_id = infer_artwork_id(source_path.relative_to(source_root), classifications)
            classification = source_classifications.get(resolved_key)
            if classification is None:
                classification = classifications.get(artwork_id) if artwork_id else None
            if classification is None or classification_family(classification) != "pixi":
                continue
            destination = destination_root / source_path.relative_to(source_root)
            plan[source_path] = (destination, classification)
    return plan


def update_database(
    *,
    root: Path,
    user_id: int,
    classifications: dict[str, dict[str, object]],
    plan: dict[Path, tuple[Path, dict[str, object]]],
) -> dict[str, int]:
    path_map = {str(source): str(destination) for source, (destination, _classification) in plan.items()}
    for source_name, destination_name in TIER_MAPPINGS:
        destination_root = root / destination_name
        if not destination_root.exists():
            continue
        for destination in destination_root.rglob("*"):
            if destination.is_file():
                path_map[str(root / source_name / destination.relative_to(destination_root))] = str(destination)
    normalized_path_map = {
        str(Path(source).resolve(strict=False)).lower(): Path(destination)
        for source, destination in path_map.items()
    }
    counts = {"source_classified": 0, "source_paths": 0, "device_paths": 0}
    now = time.time()
    with Session(engine) as session:
        source_items = session.exec(
            select(MediaSyncSourceItem).where(
                MediaSyncSourceItem.platform == "pixiv",
            )
        ).all()
        for item in source_items:
            classification = classifications.get(str(item.remote_id))
            if classification is not None:
                x_restrict = int(classification.get("x_restrict") or 0)
                tags = list(normalize_pixiv_tags(classification.get("tags")))
                item.extra_json = {
                    **(item.extra_json or {}),
                    "x_restrict": x_restrict,
                    "tags": tags,
                    "rating_family": classification_family(classification),
                }
                counts["source_classified"] += 1
            old_path = str(item.absolute_path or "")
            destination = normalized_path_map.get(str(Path(old_path).resolve(strict=False)).lower()) if old_path else None
            if destination is not None:
                item.absolute_path = str(destination)
                counts["source_paths"] += 1
            item.updated_at = now
            session.add(item)

        if path_map:
            device_rows = session.exec(
                select(DeviceFile).where(
                    or_(
                        DeviceFile.absolute_path.in_(list(path_map)),
                        DeviceFile.last_known_path.in_(list(path_map)),
                    )
                )
            ).all()
            for record in device_rows:
                absolute_destination = path_map.get(str(record.absolute_path or ""))
                last_known_destination = path_map.get(str(record.last_known_path or ""))
                if absolute_destination is not None:
                    record.absolute_path = absolute_destination
                if last_known_destination is not None:
                    record.last_known_path = last_known_destination
                record.updated_at = now
                record.last_seen_at = now
                session.add(record)
                counts["device_paths"] += 1
        session.commit()
    return counts


def rebuild_current_device_indexes(root: Path) -> dict[str, dict[str, int | bool | str | None]]:
    """让批量移动后的目录索引与磁盘重新对齐。

    媒体页在递归且可由数据库排序时会直接读取 DeviceFile；只改已有路径不足以
    覆盖此前尚未浏览、因而没有索引记录的文件，所以迁移后必须扫描目标目录。
    """
    device_id = get_device_id()
    results: dict[str, dict[str, int | bool | str | None]] = {}
    directory_names = dict.fromkeys(
        name
        for source_name, destination_name in TIER_MAPPINGS
        for name in (source_name, destination_name)
    )
    for directory_name in directory_names:
        directory_root = root / directory_name
        if not directory_root.exists():
            continue
        for attempt in range(1, 6):
            try:
                with Session(engine) as session:
                    result = scan_device_file_records(
                        DeviceFileScanRequest(
                            absolute_path=str(directory_root),
                            recursive=True,
                            hash_mode="never",
                            mark_missing_as_dangling=True,
                        ),
                        session,
                        device_id=device_id,
                    )
                break
            except OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempt >= 5:
                    raise
                time.sleep(float(attempt))
        results[directory_name] = {
            key: value
            for key, value in result.items()
            if key
            in {
                "ok",
                "processed_count",
                "hashed_count",
                "created_count",
                "rebound_count",
                "updated_count",
                "dangling_count",
            }
        }
    return results


def main() -> int:
    args = parse_args()
    root = Path(args.root_dir).expanduser().resolve()
    classifications = load_classifications(root)
    plan = build_move_plan(root, classifications)
    conflicts = [str(destination) for destination, _classification in plan.values() if destination.exists()]
    summary = {
        "mode": "apply" if args.apply else "dry-run",
        "classified_artworks": len(classifications),
        "pixi_artworks": sum(
            1
            for artwork_id, value in classifications.items()
            if classification_family(value) == "pixi"
        ),
        "planned_file_moves": len(plan),
        "planned_bytes": sum(source.stat().st_size for source in plan),
        "conflicts": conflicts[:20],
    }
    if conflicts:
        raise RuntimeError(f"pixi 目标路径已存在，拒绝覆盖: {conflicts[:5]}")
    if not args.apply:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    raise RuntimeError("Pixi/Pixiv 拆分能力已停用；请运行 scripts/merge_pixi_into_pixiv.py")


if __name__ == "__main__":
    raise SystemExit(main())
