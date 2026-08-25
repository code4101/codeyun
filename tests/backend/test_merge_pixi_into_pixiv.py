from __future__ import annotations

from pathlib import Path

import pytest

from scripts import merge_pixi_into_pixiv as merge_script


def test_build_move_plan_merges_all_tiers_without_changing_relative_paths(tmp_path: Path) -> None:
    expected: dict[Path, Path] = {}
    for level in (1, 2, 3):
        source = tmp_path / f"{level}、pixi" / "author" / f"{level}.jpg"
        source.parent.mkdir(parents=True)
        source.write_bytes(str(level).encode())
        expected[source] = tmp_path / f"{level}、pixiv" / "author" / f"{level}.jpg"

    assert merge_script.build_move_plan(tmp_path) == expected


def test_build_move_plan_refuses_to_overwrite_existing_pixiv_file(tmp_path: Path) -> None:
    source = tmp_path / "2、pixi" / "author" / "work.jpg"
    destination = tmp_path / "2、pixiv" / "author" / "work.jpg"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    destination.write_bytes(b"destination")

    with pytest.raises(RuntimeError, match="拒绝覆盖"):
        merge_script.build_move_plan(tmp_path)


def test_canonical_pixiv_path_only_rewrites_complete_pixi_tier_component() -> None:
    assert merge_script.canonical_pixiv_path(r"E:\data\1、pixi\author\work.jpg") == (
        r"E:\data\1、pixiv\author\work.jpg"
    )
    assert merge_script.canonical_pixiv_path(r"E:\data\1、pixiv\author\work.jpg") is None
