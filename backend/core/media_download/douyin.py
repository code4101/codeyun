from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from glob import escape as glob_escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from backend.core.temp_paths import codeyun_temp_root

from .batch import video_roots


_DOUYIN_ID_RE = re.compile(r"(?:/video/|^)(\d{10,})")
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DOUYIN_FORMAT_SELECTOR = "bestvideo+bestaudio/best"


@dataclass(frozen=True)
class DouyinDownloadResult:
    video_id: str
    title: str
    video_path: str
    duration: float
    width: int
    height: int
    video_codec: str
    audio_codec: str
    reused: bool = False


def parse_douyin_video_id(value: str) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc and not parsed.netloc.lower().endswith("douyin.com"):
        raise ValueError("仅支持 douyin.com 视频链接")
    match = _DOUYIN_ID_RE.search(parsed.path if parsed.scheme else text)
    if not match:
        raise ValueError("链接中没有有效的抖音视频号")
    return match.group(1)


def _safe_title(value: str) -> str:
    return _INVALID_FILENAME_RE.sub("_", str(value or "")).strip(" .") or "Douyin video"


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("找不到 ffprobe，无法校验媒体文件")
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout.decode("utf-8", errors="replace"))


def _write_browser_cookies(cookie_path: Path, cookies: list[dict[str, Any]]) -> None:
    with cookie_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            domain = str(cookie.get("domain") or "")
            if not name or "douyin.com" not in domain:
                continue
            value = str(cookie.get("value") or "").replace("\t", "").replace("\r", "").replace("\n", "")
            stream.write(
                f"{domain}\t{'TRUE' if domain.startswith('.') else 'FALSE'}\t{cookie.get('path') or '/'}\t"
                f"{'TRUE' if cookie.get('secure') else 'FALSE'}\t{int(float(cookie.get('expires') or 0))}\t"
                f"{name}\t{value}\n"
            )


def _browser_cookies(browser: Any | None) -> tuple[list[dict[str, Any]], str]:
    if browser is None:
        from DrissionPage import Chromium

        browser = Chromium()
    for tab in browser.get_tabs():
        if "douyin.com" in str(tab.url or "").lower():
            user_agent = str(tab.run_js("return navigator.userAgent") or "")
            return list(tab.cookies(all_domains=True)), user_agent
    return [], ""


def _result_from_path(path: Path, video_id: str, *, reused: bool) -> DouyinDownloadResult:
    info = _probe(path)
    streams = info.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0 or not video:
        raise RuntimeError("媒体完整性校验失败")
    title = re.sub(rf"\s*\[{re.escape(video_id)}\]\s*$", "", path.stem).strip() or path.stem
    return DouyinDownloadResult(
        video_id=video_id,
        title=title,
        video_path=str(path),
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        video_codec=str(video.get("codec_name") or ""),
        audio_codec=str(audio.get("codec_name") or ""),
        reused=reused,
    )


def _cached_result(root_dir: str | Path, video_id: str) -> DouyinDownloadResult | None:
    escaped_id = glob_escape(f"[{video_id}]")
    for root in (video_roots(root_dir).review, video_roots(root_dir).reservoir, video_roots(root_dir).library):
        matches = sorted(root.glob(f"*{escaped_id}*.mp4")) if root.exists() else []
        if matches:
            return _result_from_path(matches[0], video_id, reused=True)
    return None


def download_douyin_media(
    url: str,
    *,
    root_dir: str | Path,
    browser: Any | None = None,
    log: Callable[[str], None] = print,
) -> DouyinDownloadResult:
    """Download one explicit Douyin URL with the shared DP browser login state."""

    video_id = parse_douyin_video_id(url)
    cached = _cached_result(root_dir, video_id)
    if cached:
        log(f"{video_id} 已存在，复用本地文件。")
        return cached
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("缺少 yt-dlp 依赖，请先执行 uv sync") from exc

    work_root = codeyun_temp_root("douyin-download", video_id)
    shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)
    cookie_path = work_root / "cookies.txt"
    cookies, user_agent = _browser_cookies(browser)
    if cookies:
        _write_browser_cookies(cookie_path, cookies)

    class Logger:
        def debug(self, message: str) -> None:
            if not (message.startswith("[download]") and "%" in message):
                log(message)

        info = debug
        warning = debug

        def error(self, message: str) -> None:
            log(message)

    options: dict[str, Any] = {
        "format": DOUYIN_FORMAT_SELECTOR,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(work_root / "%(title).100B [%(id)s].%(ext)s"),
        "retries": 10,
        "fragment_retries": 10,
        "logger": Logger(),
        "http_headers": {"Referer": "https://www.douyin.com/", "User-Agent": user_agent},
    }
    if cookie_path.exists():
        options["cookiefile"] = str(cookie_path)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
    finally:
        cookie_path.unlink(missing_ok=True)

    files = sorted(work_root.glob("*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not files:
        raise RuntimeError("下载完成但没有生成 MP4 文件")
    source = files[0]
    title = _safe_title(str((info or {}).get("title") or source.stem))
    reservoir = video_roots(root_dir).reservoir
    reservoir.mkdir(parents=True, exist_ok=True)
    target = reservoir / f"{title} [{video_id}].mp4"
    shutil.move(str(source), str(target))
    result = _result_from_path(target, video_id, reused=False)
    shutil.rmtree(work_root, ignore_errors=True)
    return result
