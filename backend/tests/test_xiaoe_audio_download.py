from pathlib import Path

from scripts.download_xiaoe_audio import (
    _audio_info_is_empty,
    _audio_download_error_reason,
    _new_full_state,
    _next_cursor,
    _reconcile_downloaded_duplicates,
    _title_key,
    _video_title_keys,
)


def test_audio_info_detects_platform_empty_media() -> None:
    assert _audio_info_is_empty({"audio_size": 0, "audio_length": 0}) is True
    assert _audio_info_is_empty({"audio_size": 12.5, "audio_length": 60}) is False
    assert _audio_info_is_empty({"audio_url": "https://cdn.test/audio.mp3"}) is False


def test_audio_download_error_reason_hides_source_url() -> None:
    error = RuntimeError(
        "HTTP error 403 Forbidden: https://cdn.test/private/audio.mp3?signature=secret"
    )
    assert _audio_download_error_reason(error) == "音频源拒绝访问"


def test_title_key_normalizes_badges_and_windows_filename_characters() -> None:
    assert _title_key("课程: 第一讲\n免费") == _title_key("课程_ 第一讲")
    assert _title_key("20211202 第04届念住初阶网课答疑") == _title_key(
        "第04届念住初阶网课答疑"
    )
    assert _title_key("20230127 第10、11届念住初阶网课答疑") == _title_key(
        "20230127第10、11届念住初阶网课答疑-8"
    )


def test_video_title_keys_read_layered_video_archive(tmp_path: Path) -> None:
    year = tmp_path / "视频" / "2025"
    year.mkdir(parents=True)
    (year / "20250101_010203_同名课程.mp4").write_bytes(b"video")
    assert _video_title_keys(tmp_path) == {"同名课程"}


def test_audio_cursor_advances_across_pages() -> None:
    assert _next_cursor(3, 8) == {"page": 3, "item_index": 9}
    assert _next_cursor(3, 9) == {"page": 4, "item_index": 0}


def test_full_state_resumes_failed_cursor(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"status":"failed","cursor":{"page":7,"item_index":2},"downloaded_count":5}',
        encoding="utf-8",
    )
    state = _new_full_state(path)
    assert state["cursor"] == {"page": 7, "item_index": 2}
    assert state["downloaded_count"] == 5


def test_reconcile_downloaded_duplicate_removes_only_indexed_audio(tmp_path: Path) -> None:
    audio_path = tmp_path / "音频" / "2026" / "duplicate.mp3"
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(b"audio")
    index_path = tmp_path / "_下载辅助" / "audio-catalog-index.json"
    index = {
        "items": {
            "a_1": {
                "title": "20211202 第04届念住初阶网课答疑",
                "outcome": "downloaded",
                "result": {"path": str(audio_path)},
            }
        }
    }

    removed = _reconcile_downloaded_duplicates(
        tmp_path,
        index_path,
        index,
        {_title_key("第04届念住初阶网课答疑")},
    )

    assert removed == 1
    assert not audio_path.exists()
    assert index["items"]["a_1"]["outcome"] == "skipped_video_title"
