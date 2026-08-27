from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from backend.plugins.modules.media_sync.reservoir import (
    ReservoirUnit,
    count_media_files,
    discover_reservoir_units,
    media_reservoir_root,
    media_review_root,
    plan_reservoir_refill,
    refill_review_batch,
)


def _write(path: Path, content: bytes = b"image") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_refill_review_batch_moves_newest_media_until_limit(tmp_path: Path) -> None:
    review_root = media_review_root(tmp_path, "pixiv")
    reservoir_root = media_reservoir_root(tmp_path, "pixiv")
    _write(review_root / "existing.jpg")
    first = _write(reservoir_root / "author" / "first.jpg")
    second = _write(reservoir_root / "author" / "second.png")
    third = _write(reservoir_root / "author" / "third.gif")
    now = time.time()
    os.utime(first, (now - 300, now - 300))
    os.utime(second, (now - 200, now - 200))
    os.utime(third, (now - 100, now - 100))

    result = refill_review_batch(tmp_path, "pixiv", limit=3)

    assert result.before_media_count == 1
    assert result.after_media_count == 3
    assert result.moved_media_count == 2
    assert (review_root / "author" / "second.png").is_file()
    assert (review_root / "author" / "third.gif").is_file()
    assert (reservoir_root / "author" / "first.jpg").is_file()


def test_pixiv_ugoira_sidecar_moves_with_media_without_consuming_extra_slot(tmp_path: Path) -> None:
    reservoir_root = media_reservoir_root(tmp_path, "pixiv")
    gif_path = _write(reservoir_root / "artist" / "100_work.gif")
    sidecar_path = _write(reservoir_root / "artist" / "100_work.ugoira.json", b"{}")

    units = discover_reservoir_units(tmp_path, "pixiv")
    result = refill_review_batch(tmp_path, "pixiv", limit=1, units=units)
    review_root = media_review_root(tmp_path, "pixiv")

    assert len(units) == 1
    assert units[0].media_count == 1
    assert result.moved_media_count == 1
    assert (review_root / "artist" / gif_path.name).is_file()
    assert (review_root / "artist" / sidecar_path.name).is_file()


def test_explicit_multi_media_unit_is_not_split_when_capacity_is_too_small(tmp_path: Path) -> None:
    reservoir_root = media_reservoir_root(tmp_path, "pinterest")
    _write(reservoir_root / "pin-1" / "01.jpg")
    _write(reservoir_root / "pin-1" / "02.jpg")
    unit = ReservoirUnit(
        key="pin-1",
        relative_paths=(Path("pin-1/01.jpg"), Path("pin-1/02.jpg")),
        media_count=2,
    )

    assert plan_reservoir_refill(tmp_path, "pinterest", limit=1, units=[unit]) == []
    result = refill_review_batch(tmp_path, "pinterest", limit=1, units=[unit])

    assert result.moved_unit_count == 0
    assert count_media_files(reservoir_root) == 2
    assert count_media_files(media_review_root(tmp_path, "pinterest")) == 0


def test_refill_skips_unit_when_any_destination_conflicts(tmp_path: Path) -> None:
    reservoir_root = media_reservoir_root(tmp_path, "pinterest")
    review_root = media_review_root(tmp_path, "pinterest")
    conflicting = _write(reservoir_root / "pin.jpg", b"new")
    _write(review_root / "pin.jpg", b"existing")
    next_path = _write(reservoir_root / "next.jpg", b"next")
    now = time.time()
    os.utime(conflicting, (now - 100, now - 100))
    os.utime(next_path, (now - 200, now - 200))

    result = refill_review_batch(tmp_path, "pinterest", limit=2)

    assert result.skipped_unit_count == 1
    assert result.moved_media_count == 1
    assert (reservoir_root / "pin.jpg").read_bytes() == b"new"
    assert (review_root / "pin.jpg").read_bytes() == b"existing"
    assert (review_root / "next.jpg").read_bytes() == b"next"


def test_refill_rejects_path_outside_reservoir(tmp_path: Path) -> None:
    unit = ReservoirUnit(key="escape", relative_paths=(Path("../escape.jpg"),), media_count=1)

    with pytest.raises(ValueError, match="路径越界"):
        refill_review_batch(tmp_path, "pixiv", units=[unit])


def test_legacy_pixi_platform_aliases_to_pixiv_directory(tmp_path: Path) -> None:
    assert media_reservoir_root(tmp_path, "PIXIV").name == "3、pixiv"
    assert media_review_root(tmp_path, "pixiv").name == "2、pixiv"
    assert media_reservoir_root(tmp_path, "pixi").name == "3、pixiv"
    assert media_review_root(tmp_path, "pixi").name == "2、pixiv"
    assert media_reservoir_root(tmp_path, "video").name == "3、video"
    assert media_review_root(tmp_path, "video").name == "2、video"

    with pytest.raises(ValueError, match="不支持的媒体平台"):
        media_reservoir_root(tmp_path, "unknown")
