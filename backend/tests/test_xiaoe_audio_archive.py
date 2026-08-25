from pathlib import Path

from backend.core.xiaoe_audio_archive import (
    AudioProbe,
    build_audio_archive_filename,
    build_audio_archive_path,
    download_audio_file,
)


def test_build_audio_archive_filename_uses_timestamp_prefix() -> None:
    assert build_audio_archive_filename("课程:第一讲", "2026-08-03 08:09:10") == (
        "20260803_080910_课程_第一讲.mp3"
    )


def test_build_audio_archive_path_uses_audio_year_directory(tmp_path: Path) -> None:
    assert build_audio_archive_path(tmp_path, "课程", "2024-05-06 07:08:09") == (
        tmp_path / "音频" / "2024" / "20240506_070809_课程.mp3"
    )


def test_download_audio_transcodes_non_mp3_source(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if len(commands) == 1:
            return type("Result", (), {"returncode": 1, "stderr": "not mp3", "stdout": ""})()
        Path(command[-1]).write_bytes(b"converted-mp3")
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr("backend.core.xiaoe_audio_archive.run_quiet", fake_run)
    monkeypatch.setattr(
        "backend.core.xiaoe_audio_archive.probe_audio",
        lambda path, ffprobe=None: AudioProbe(60.0, path.stat().st_size, "mp3", 44100, 2),
    )

    result = download_audio_file(
        audio_url="https://cdn.test/source.m4a",
        title="非 MP3 音频",
        published_at="2025-10-27 12:22:50",
        output_dir=tmp_path,
        ffmpeg="ffmpeg",
    )

    assert commands[0][commands[0].index("-c") + 1] == "copy"
    assert commands[1][commands[1].index("-c:a") + 1] == "libmp3lame"
    assert Path(result["path"]).read_bytes() == b"converted-mp3"
