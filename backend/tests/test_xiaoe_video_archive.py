from pathlib import Path

from backend.core.xiaoe_video_archive import build_archive_filename, build_archive_path


def test_build_archive_filename_adds_published_time_prefix() -> None:
    assert (
        build_archive_filename(
            "【巡香】棒喝之棒-惟海法师-20230602Am0927",
            "2026-07-11 18:45:25",
        )
        == "20260711_184525_【巡香】棒喝之棒-惟海法师-20230602Am0927.mp4"
    )


def test_build_archive_filename_replaces_windows_invalid_characters() -> None:
    filename = build_archive_filename('课次: A/B? "测试"', "2026-01-02 03:04:05")
    assert filename == "20260102_030405_课次_ A_B_ _测试_.mp4"
    assert Path(filename).suffix == ".mp4"


def test_build_archive_path_groups_by_published_year(tmp_path: Path) -> None:
    assert build_archive_path(tmp_path, "测试视频", "2025-11-03 14:23:32") == (
        tmp_path / "视频" / "2025" / "20251103_142332_测试视频.mp4"
    )
