import json
from pathlib import Path

from backend.core.xiaoe_media_layout import migrate_legacy_video_layout


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
