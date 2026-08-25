from __future__ import annotations

import json
import re
import shutil
import subprocess
from glob import escape as glob_escape
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from backend.core.temp_paths import codeyun_temp_root

from .batch import video_roots


_BVID_RE = re.compile(r"\b(BV[0-9A-Za-z]{10})\b", re.IGNORECASE)
_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
BILIBILI_FORMAT_SELECTOR = "bestvideo+bestaudio/best"


@dataclass(frozen=True)
class BilibiliDownloadResult:
    bvid: str
    title: str
    video_path: str
    duration: float
    width: int
    height: int
    video_codec: str
    audio_codec: str
    format_ids: tuple[str, ...] = ()
    reused: bool = False


def parse_bvid(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme and parsed.netloc and not parsed.netloc.lower().endswith("bilibili.com"):
        raise ValueError("仅支持 bilibili.com 视频链接")
    match = _BVID_RE.search(str(value or ""))
    if not match:
        raise ValueError("链接中没有有效的 BVID")
    return "BV" + match.group(1)[2:]


def _safe_title(value: str) -> str:
    return _INVALID_FILENAME_RE.sub("_", str(value or "")).strip(" .") or "Bilibili video"


def _title_from_downloaded_filename(path: Path, bvid: str) -> str:
    return re.sub(rf"\s*\[{re.escape(bvid)}\]\s*\d+P$", "", path.stem, flags=re.IGNORECASE).strip() or path.stem


def _write_browser_cookies(cookie_path: Path, cookies: list[dict[str, Any]]) -> None:
    with cookie_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# Netscape HTTP Cookie File\n")
        for cookie in cookies:
            name = str(cookie.get("name") or "")
            if not name:
                continue
            domain = str(cookie.get("domain") or ".bilibili.com")
            value = str(cookie.get("value") or "").replace("\t", "").replace("\r", "").replace("\n", "")
            include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if cookie.get("secure") else "FALSE"
            expires = int(float(cookie.get("expires") or 0))
            stream.write(
                f"{domain}\t{include_subdomains}\t{cookie.get('path') or '/'}\t"
                f"{secure}\t{expires}\t{name}\t{value}\n"
            )


def _browser_cookies(browser: Any | None) -> list[dict[str, Any]]:
    if browser is None:
        try:
            from DrissionPage import Chromium

            browser = Chromium()
        except Exception:
            return []
    for tab in browser.get_tabs():
        if "bilibili.com" in str(tab.url or "").lower():
            return list(tab.cookies(all_domains=True))
    return []


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
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _cached_result(root_dir: str | Path, bvid: str) -> BilibiliDownloadResult | None:
    roots = video_roots(root_dir)
    for root in (roots.review, roots.reservoir, roots.library):
        escaped_bvid = glob_escape(f"[{bvid}]")
        matches = sorted(root.glob(f"*{escaped_bvid}*.mp4")) if root.exists() else []
        if not matches:
            continue
        video_path = matches[0]
        info = _probe(video_path)
        streams = info.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), {})
        audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
        return BilibiliDownloadResult(
            bvid=bvid,
            title=_title_from_downloaded_filename(video_path, bvid),
            video_path=str(video_path),
            duration=float((info.get("format") or {}).get("duration") or 0),
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            video_codec=str(video.get("codec_name") or ""),
            audio_codec=str(audio.get("codec_name") or ""),
            reused=True,
        )
    return None


def refresh_bilibili_result_path(
    result: BilibiliDownloadResult,
    *,
    root_dir: str | Path,
) -> BilibiliDownloadResult:
    """Resolve the current path after a reservoir-to-review batch move."""

    if Path(result.video_path).is_file():
        return result
    current = _cached_result(root_dir, result.bvid)
    if current is None:
        raise FileNotFoundError(f"找不到已下载视频 {result.bvid}")
    return replace(current, title=result.title, reused=result.reused)


def download_bilibili_media(
    url: str,
    *,
    root_dir: str | Path,
    browser: Any | None = None,
    log: Callable[[str], None] = print,
) -> BilibiliDownloadResult:
    """Download the highest stream normally available to the browser session.

    Video and audio are fetched separately and remuxed without transcoding.  The
    function does not discover URLs, bypass membership, paid content, or DRM.
    """

    bvid = parse_bvid(url)
    cached = _cached_result(root_dir, bvid)
    if cached:
        log(f"{bvid} 已存在，复用本地文件。")
        return cached

    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("缺少 yt-dlp 依赖，请先执行 uv sync") from exc

    work_root = codeyun_temp_root("bilibili-download", bvid)
    shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)
    cookie_path = work_root / "cookies.txt"
    cookies = _browser_cookies(browser)
    if cookies:
        _write_browser_cookies(cookie_path, cookies)

    class Logger:
        def debug(self, message: str) -> None:
            if message.startswith("[download]") and "%" in message:
                return
            log(message)

        info = debug
        warning = debug

        def error(self, message: str) -> None:
            log(message)

    options: dict[str, Any] = {
        "format": BILIBILI_FORMAT_SELECTOR,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "outtmpl": str(work_root / "%(title).120B [%(id)s] %(height)sP.%(ext)s"),
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "logger": Logger(),
        "http_headers": {"Referer": f"https://www.bilibili.com/video/{bvid}/"},
    }
    if cookie_path.exists():
        options["cookiefile"] = str(cookie_path)
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(f"https://www.bilibili.com/video/{bvid}/", download=True)
    finally:
        cookie_path.unlink(missing_ok=True)

    video_files = sorted(work_root.glob("*.mp4"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    if not video_files:
        raise RuntimeError("下载完成但没有生成 MP4 文件")
    video_path = video_files[0]
    title = _safe_title(str((info or {}).get("title") or video_path.stem))
    probe = _probe(video_path)
    duration = float((probe.get("format") or {}).get("duration") or 0)
    streams = probe.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
    if duration <= 0 or not video_stream or not audio_stream:
        raise RuntimeError("媒体完整性校验失败")

    reservoir = video_roots(root_dir).reservoir
    reservoir.mkdir(parents=True, exist_ok=True)
    final_video = reservoir / video_path.name
    shutil.move(str(video_path), str(final_video))
    result = BilibiliDownloadResult(
        bvid=bvid,
        title=title,
        video_path=str(final_video),
        duration=duration,
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        video_codec=str(video_stream.get("codec_name") or ""),
        audio_codec=str(audio_stream.get("codec_name") or ""),
        format_ids=tuple(
            str(item.get("format_id") or "")
            for item in ((info or {}).get("requested_formats") or [])
            if str(item.get("format_id") or "")
        ),
    )
    shutil.rmtree(work_root, ignore_errors=True)
    return result
