from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.core.media_download.batch import VIDEO_REVIEW_LIMIT, refill_video_review_batch, video_roots
from backend.core.media_download.bilibili import (
    BILIBILI_FORMAT_SELECTOR,
    BilibiliDownloadResult,
    _title_from_downloaded_filename,
    parse_bvid,
    refresh_bilibili_result_path,
)
from backend.core.media_download.html_document import video_document_path, write_video_html_document
from backend.core.media_download.douyin import DOUYIN_FORMAT_SELECTOR, parse_douyin_video_id


def test_parse_bvid_accepts_canonical_and_shared_urls() -> None:
    assert parse_bvid("https://www.bilibili.com/video/BV1K4411m7jx/") == "BV1K4411m7jx"
    assert parse_bvid("BV1K4411m7jx") == "BV1K4411m7jx"


def test_parse_bvid_rejects_unrelated_hosts() -> None:
    with pytest.raises(ValueError, match="仅支持"):
        parse_bvid("https://example.com/video/BV1K4411m7jx")


def test_downloader_does_not_impose_a_resolution_ceiling() -> None:
    assert BILIBILI_FORMAT_SELECTOR == "bestvideo+bestaudio/best"
    assert DOUYIN_FORMAT_SELECTOR == "bestvideo+bestaudio/best"


def test_parse_douyin_video_id_accepts_canonical_url() -> None:
    assert parse_douyin_video_id("https://www.douyin.com/video/7444554082850704677") == "7444554082850704677"


def test_parse_douyin_video_id_rejects_unrelated_hosts() -> None:
    with pytest.raises(ValueError, match="仅支持"):
        parse_douyin_video_id("https://example.com/video/7444554082850704677")


def test_cached_title_drops_storage_identity_suffix() -> None:
    path = Path("Music [BV1K4411m7jx] 1080P.mp4")
    assert _title_from_downloaded_filename(path, "BV1K4411m7jx") == "Music"


def test_video_batch_moves_one_complete_mp4_unit(tmp_path: Path) -> None:
    roots = video_roots(tmp_path)
    roots.reservoir.mkdir(parents=True)
    video = roots.reservoir / "Music [BV1K4411m7jx] 1080P.mp4"
    video.write_bytes(b"video")
    write_video_html_document(
        video,
        title="Music",
        source_url="https://www.bilibili.com/video/BV1K4411m7jx/",
        summary="Summary",
    )
    os.utime(video, ns=(1, 1))

    result = refill_video_review_batch(tmp_path)

    assert VIDEO_REVIEW_LIMIT == 20
    assert result == {"limit": 20, "before": 0, "after": 1, "moved": 1}
    assert (roots.review / video.name).is_file()
    assert video_document_path(roots.review / video.name).is_file()
    assert not video_document_path(video).exists()


def test_html_document_uses_same_prefix_and_relative_video(tmp_path: Path) -> None:
    video = tmp_path / "演示视频.mp4"
    video.write_bytes(b"video")

    document = write_video_html_document(
        video,
        title="演示 <视频>",
        source_url="https://example.com/watch?v=1&from=test",
        summary="可反查的摘要",
        timeline=[{"time": 8, "label": "00:08", "description": "正文画面"}],
    )
    content = document.read_text(encoding="utf-8")

    assert document == video_document_path(video)
    assert document.name == "演示视频.html"
    assert '%E6%BC%94%E7%A4%BA%E8%A7%86%E9%A2%91.mp4' in content
    assert 'data-time="8.000"' in content
    assert "演示 &lt;视频&gt;" in content
    assert "autoplay" not in content


def test_download_result_path_is_refreshed_after_batch_move(tmp_path: Path, monkeypatch) -> None:
    stale = BilibiliDownloadResult(
        bvid="BV1K4411m7jx",
        title="Music",
        video_path=str(tmp_path / "3、video" / "missing.mp4"),
        duration=10,
        width=1920,
        height=1080,
        video_codec="h264",
        audio_codec="aac",
    )
    current_path = tmp_path / "2、video" / "Music.mp4"
    current_path.parent.mkdir(parents=True)
    current_path.write_bytes(b"video")
    current = BilibiliDownloadResult(**{**stale.__dict__, "video_path": str(current_path), "reused": True})
    monkeypatch.setattr("backend.core.media_download.bilibili._cached_result", lambda *_: current)

    refreshed = refresh_bilibili_result_path(stale, root_dir=tmp_path)

    assert refreshed.video_path == str(current_path)
    assert refreshed.reused is False


def test_video_batch_never_exceeds_twenty_items(tmp_path: Path) -> None:
    roots = video_roots(tmp_path)
    roots.reservoir.mkdir(parents=True)
    for index in range(25):
        (roots.reservoir / f"Video {index:02d} [BV{index:010d}].mp4").write_bytes(b"video")

    result = refill_video_review_batch(tmp_path)

    assert result["after"] == 20
    assert len(list(roots.reservoir.glob("*.mp4"))) == 5
