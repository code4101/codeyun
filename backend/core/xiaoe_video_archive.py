from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.core.services.launcher import run_quiet


INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class VideoProbe:
    duration_seconds: float
    size_bytes: int
    video_codec: str
    width: int
    height: int
    audio_codec: str
    sample_rate: int
    channels: int


def build_archive_filename(title: str, published_at: str) -> str:
    timestamp = datetime.strptime(published_at.strip(), "%Y-%m-%d %H:%M:%S")
    safe_title = INVALID_FILENAME_CHARS.sub("_", title).strip().rstrip(".")
    if not safe_title:
        raise ValueError("视频名称为空")
    return f"{timestamp:%Y%m%d_%H%M%S}_{safe_title}.mp4"


def build_archive_path(output_dir: Path, title: str, published_at: str) -> Path:
    """按上架年份生成视频归档路径。"""
    timestamp = datetime.strptime(published_at.strip(), "%Y-%m-%d %H:%M:%S")
    return output_dir / "视频" / f"{timestamp:%Y}" / build_archive_filename(title, published_at)


def probe_video(path: Path, *, ffprobe: str | None = None) -> VideoProbe:
    ffprobe_bin = ffprobe or shutil.which("ffprobe")
    if not ffprobe_bin:
        raise RuntimeError("找不到 ffprobe")
    completed = run_quiet(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size:"
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels"
            ),
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
    streams = payload.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise RuntimeError("下载文件缺少视频轨道或音频轨道")
    format_info = payload.get("format") or {}
    return VideoProbe(
        duration_seconds=float(format_info.get("duration") or 0),
        size_bytes=int(format_info.get("size") or path.stat().st_size),
        video_codec=str(video.get("codec_name") or ""),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        audio_codec=str(audio.get("codec_name") or ""),
        sample_rate=int(audio.get("sample_rate") or 0),
        channels=int(audio.get("channels") or 0),
    )


def download_hls_video(
    *,
    playlist_url: str,
    title: str,
    published_at: str,
    output_dir: Path,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> dict[str, Any]:
    final_path = build_archive_path(output_dir, title, published_at)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = final_path.with_name(f"{final_path.name}.downloading.mp4")

    if final_path.exists():
        probe = probe_video(final_path, ffprobe=ffprobe)
        return {
            "status": "skipped_existing",
            "path": str(final_path),
            "probe": asdict(probe),
        }

    ffmpeg_bin = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("找不到 ffmpeg")
    if temp_path.exists():
        temp_path.unlink()

    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-y",
        "-rw_timeout",
        "30000000",
        "-protocol_whitelist",
        "file,http,https,tcp,tls,crypto,data,httpproxy",
        "-allowed_extensions",
        "ALL",
        "-i",
        playlist_url,
        "-map",
        "0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(temp_path),
    ]
    completed = run_quiet(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "ffmpeg 下载失败").strip()
        raise RuntimeError(detail[-4000:])

    probe = probe_video(temp_path, ffprobe=ffprobe)
    if probe.duration_seconds <= 0 or probe.size_bytes <= 0:
        raise RuntimeError("下载文件没有有效时长或大小")
    temp_path.replace(final_path)
    return {
        "status": "downloaded",
        "path": str(final_path),
        "probe": asdict(probe),
    }
