from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .html_document import video_document_path


VIDEO_REVIEW_LIMIT = 20
VIDEO_MEDIA_SUFFIXES = frozenset({".mov", ".mp4", ".webm"})
_BVID_RE = re.compile(r"\[(BV[0-9A-Za-z]+)\]", re.IGNORECASE)


@dataclass(frozen=True)
class VideoRoots:
    library: Path
    review: Path
    reservoir: Path


def video_roots(root_dir: str | Path) -> VideoRoots:
    root = Path(root_dir).expanduser().resolve()
    return VideoRoots(
        library=root / "1、video",
        review=root / "2、video",
        reservoir=root / "3、video",
    )


def iter_video_files(root: str | Path) -> list[Path]:
    path = Path(root)
    if not path.exists():
        return []
    return sorted(
        (item for item in path.rglob("*") if item.is_file() and item.suffix.lower() in VIDEO_MEDIA_SUFFIXES),
        key=lambda item: (item.stat().st_mtime_ns, item.as_posix().lower()),
    )


def _unit_key(path: Path) -> str:
    match = _BVID_RE.search(path.name)
    return match.group(1).upper() if match else path.stem.lower()


def refill_video_review_batch(
    root_dir: str | Path,
    *,
    limit: int = VIDEO_REVIEW_LIMIT,
) -> dict[str, int]:
    """Move complete video units from the reservoir into the review batch.

    The limit counts playable video files. Downloaded MP4 files already contain
    their selected audio track, so no redundant standalone audio is managed.
    """

    roots = video_roots(root_dir)
    roots.review.mkdir(parents=True, exist_ok=True)
    roots.reservoir.mkdir(parents=True, exist_ok=True)
    normalized_limit = max(int(limit), 0)
    before = len(iter_video_files(roots.review))
    remaining = max(normalized_limit - before, 0)
    if remaining == 0:
        return {"limit": normalized_limit, "before": before, "after": before, "moved": 0}

    units: dict[str, list[Path]] = {}
    for media in iter_video_files(roots.reservoir):
        units.setdefault(_unit_key(media), []).append(media)
    ordered = sorted(
        units.values(),
        key=lambda paths: (min(path.stat().st_mtime_ns for path in paths), paths[0].as_posix().lower()),
    )
    moved = 0
    for paths in ordered:
        video_count = sum(path.suffix.lower() in VIDEO_MEDIA_SUFFIXES for path in paths)
        if video_count <= 0 or video_count > remaining:
            continue
        companions = []
        for path in paths:
            document = video_document_path(path)
            if document.is_file():
                companions.append(document)
        unit_paths = [*paths, *companions]
        destinations = [roots.review / path.relative_to(roots.reservoir) for path in unit_paths]
        if any(destination.exists() for destination in destinations):
            continue
        for source, destination in zip(unit_paths, destinations, strict=True):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
        moved += video_count
        remaining -= video_count
        if remaining == 0:
            break
    return {
        "limit": normalized_limit,
        "before": before,
        "after": len(iter_video_files(roots.review)),
        "moved": moved,
    }
