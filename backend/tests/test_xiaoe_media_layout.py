import json
from pathlib import Path

from backend.core.xiaoe_media_layout import migrate_legacy_video_layout, consolidate_video_archives


def test_migrate_legacy_video_layout_moves_files_and_rewrites_state(tmp_path: Path) -> None:
    source = tmp_path / "2025"
    source.mkdir()
    old_path = source / "20250101_010203_课程.mp4"
    old_path.write_bytes(b"video")
    helper = tmp_path / "_下载辅助"
    helper.mkdir()
    state_path = helper / "current-state.json"
    state_path.write_text(
        json.dumps({"path": str(old_path)}, ensure_ascii=False), encoding="utf-8"
    )

    result = migrate_legacy_video_layout(tmp_path)

    target = tmp_path / "视频" / "2025" / old_path.name
    assert target.read_bytes() == b"video"
    assert not source.exists()
    assert json.loads(state_path.read_text(encoding="utf-8"))["path"] == str(target)
    assert result == {
        "moved_files": 1,
        "removed_legacy_year_dirs": 1,
        "rewritten_state_files": 1,
    }


def test_consolidation_preserves_source_and_rebases_index(tmp_path: Path) -> None:
    source, target = tmp_path / "source", tmp_path / "target"
    source_file = source / "视频" / "2026" / "course.mp4"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"verified video")
    helper = source / "_下载辅助"
    helper.mkdir()
    (helper / "catalog-index.json").write_text(json.dumps({"items": {"key": {
        "outcome": "downloaded", "result": {"path": str(source_file)}}}}), encoding="utf-8")
    assert consolidate_video_archives(source, target) == {"copied": 1, "existing": 0, "indexed": 1}
    assert source_file.exists()
    target_file = target / source_file.relative_to(source)
    assert target_file.read_bytes() == source_file.read_bytes()
    index = json.loads((target / "_下载辅助" / "catalog-index.json").read_text(encoding="utf-8"))
    assert index["items"]["key"]["result"]["path"] == str(target_file)
    assert consolidate_video_archives(source, target)["existing"] == 1


def test_consolidation_stops_on_conflict(tmp_path: Path) -> None:
    import pytest
    source, target = tmp_path / "source", tmp_path / "target"
    for root, content in [(source, b"source"), (target, b"target")]:
        path = root / "视频" / "2026" / "course.mp4"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    with pytest.raises(FileExistsError):
        consolidate_video_archives(source, target)
    assert (target / "视频" / "2026" / "course.mp4").read_bytes() == b"target"
