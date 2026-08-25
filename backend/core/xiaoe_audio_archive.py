from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.xiaoe_video_archive import INVALID_FILENAME_CHARS
from backend.core.services.launcher import run_quiet


@dataclass(frozen=True)
class AudioProbe:
    duration_seconds: float
    size_bytes: int
    audio_codec: str
    sample_rate: int
    channels: int


def build_audio_archive_filename(title: str, published_at: str) -> str:
    timestamp = datetime.strptime(published_at.strip(), "%Y-%m-%d %H:%M:%S")
    safe_title = INVALID_FILENAME_CHARS.sub("_", title).strip().rstrip(".")
    if not safe_title:
        raise ValueError("音频名称为空")
    return f"{timestamp:%Y%m%d_%H%M%S}_{safe_title}.mp3"


def build_audio_archive_path(output_dir: Path, title: str, published_at: str) -> Path:
    timestamp = datetime.strptime(published_at.strip(), "%Y-%m-%d %H:%M:%S")
    return output_dir / "音频" / f"{timestamp:%Y}" / build_audio_archive_filename(
        title, published_at
    )


def probe_audio(path: Path, *, ffprobe: str | None = None) -> AudioProbe:
    ffprobe_bin = ffprobe or shutil.which("ffprobe")
    if not ffprobe_bin:
        raise RuntimeError("找不到 ffprobe")
    completed = run_quiet(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    audio = next(
        (item for item in payload.get("streams") or [] if item.get("codec_type") == "audio"),
        None,
    )
    if not audio:
        raise RuntimeError("下载文件缺少音频轨道")
    format_info = payload.get("format") or {}
    return AudioProbe(
        duration_seconds=float(format_info.get("duration") or 0),
        size_bytes=int(format_info.get("size") or path.stat().st_size),
        audio_codec=str(audio.get("codec_name") or ""),
        sample_rate=int(audio.get("sample_rate") or 0),
        channels=int(audio.get("channels") or 0),
    )


def download_audio_file(
    *,
    audio_url: str,
    title: str,
    published_at: str,
    output_dir: Path,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> dict[str, Any]:
    final_path = build_audio_archive_path(output_dir, title, published_at)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(f"{final_path.name}.downloading.mp3")
    if final_path.exists():
        probe = probe_audio(final_path, ffprobe=ffprobe)
        return {"status": "skipped_existing", "path": str(final_path), "probe": asdict(probe)}

    ffmpeg_bin = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("找不到 ffmpeg")
    temp_path.unlink(missing_ok=True)
    copy_command = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-y",
            "-rw_timeout",
            "30000000",
            "-i",
            audio_url,
            "-map",
            "0:a:0",
            "-c",
            "copy",
            str(temp_path),
        ]
    completed = run_quiet(
        copy_command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        temp_path.unlink(missing_ok=True)
        transcode_command = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-stats",
            "-y",
            "-rw_timeout",
            "30000000",
            "-i",
            audio_url,
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(temp_path),
        ]
        completed = run_quiet(
            transcode_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "ffmpeg 下载失败").strip()
            raise RuntimeError(detail[-4000:])

    probe = probe_audio(temp_path, ffprobe=ffprobe)
    if probe.duration_seconds <= 0 or probe.size_bytes <= 0:
        raise RuntimeError("下载文件没有有效时长或大小")
    temp_path.replace(final_path)
    return {"status": "downloaded", "path": str(final_path), "probe": asdict(probe)}
