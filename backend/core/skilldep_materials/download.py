"""Bounded streaming download of one public material file.

Never reads past ``max_bytes``: a larger declared ``Content-Length`` is refused
before the body is read, and an undeclared body is cut off at the cap. Bytes go
to a sibling ``.part`` file that only replaces the target after the full body
arrived, so a failure never leaves a file that looks complete.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

CHUNK = 1024 * 256
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CodeYun-skilldep-materials/1.0'


class DownloadTooLarge(RuntimeError):
    pass


@dataclass
class Downloaded:
    path: Path
    size_bytes: int
    sha256: str
    content_type: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def filename_for(url: str, fallback: str) -> str:
    """Readable, filesystem-safe name taken from the URL path."""
    name = unquote(Path(urlparse(url).path).name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    return name[:150] or fallback


def fetch_to_file(url: str, target: Path, *, max_bytes: int, timeout: float = 60,
                  session: requests.Session | None = None,
                  on_progress: Callable[[int], object] | None = None) -> Downloaded:
    """``on_progress(bytes_written)`` is called after each chunk (see ``progress.ProgressReporter``)."""
    if max_bytes <= 0:
        raise DownloadTooLarge('没有剩余下载额度')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    http = session or requests.Session()
    digest = hashlib.sha256()
    size = 0
    try:
        with http.get(url, stream=True, timeout=timeout, headers={'User-Agent': USER_AGENT},
                      allow_redirects=True) as response:
            response.raise_for_status()
            declared = response.headers.get('Content-Length')
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise DownloadTooLarge(f'声明大小 {declared} 超过上限 {max_bytes}')
            with part.open('wb') as stream:
                for chunk in response.iter_content(CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadTooLarge(f'实际大小超过上限 {max_bytes}')
                    digest.update(chunk)
                    stream.write(chunk)
                    if on_progress:
                        on_progress(size)
            # Content-Length counts encoded bytes; only plain bodies can be compared.
            encoded = response.headers.get('Content-Encoding', 'identity') not in ('', 'identity')
            if declared and declared.isdigit() and not encoded and int(declared) != size:
                raise RuntimeError(f'下载不完整：{size}/{declared}')
            content_type = response.headers.get('Content-Type', '')
        if size == 0:
            raise RuntimeError('下载内容为空')
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return Downloaded(target, size, digest.hexdigest(), content_type)
