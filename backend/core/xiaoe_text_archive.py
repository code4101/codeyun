from __future__ import annotations

import hashlib
import html
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from backend.core.xiaoe_video_archive import INVALID_FILENAME_CHARS


STYLE_URL_PATTERN = re.compile(r"url\((['\"]?)(.*?)\1\)", re.IGNORECASE)
IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}


def build_text_archive_dir(output_dir: Path, title: str, published_at: str) -> Path:
    timestamp = datetime.strptime(published_at.strip(), "%Y-%m-%d %H:%M:%S")
    safe_title = INVALID_FILENAME_CHARS.sub("_", title).strip().rstrip(".")
    if not safe_title:
        raise ValueError("图文名称为空")
    return output_dir / "图文" / f"{timestamp:%Y}" / f"{timestamp:%Y%m%d_%H%M%S}_{safe_title}"


def _absolute_image_url(url: str) -> str:
    value = html.unescape(url.strip())
    if value.startswith("//"):
        return "https:" + value
    return value


def _download_remote_image(url: str, target_stem: Path) -> Path:
    normalized_url = _absolute_image_url(url)
    for existing in target_stem.parent.glob(target_stem.name + ".*"):
        if existing.is_file() and ".downloading" not in existing.name:
            return existing
    request = Request(normalized_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as response:
        content_type = response.headers.get_content_type().lower()
        extension = IMAGE_CONTENT_TYPE_EXTENSIONS.get(content_type)
        if not extension:
            extension = Path(urlparse(normalized_url).path).suffix.lower()
        if not extension or len(extension) > 8:
            extension = mimetypes.guess_extension(content_type) or ".img"
        final_path = target_stem.with_suffix(extension)
        temp_path = final_path.with_name(final_path.name + ".downloading")
        with temp_path.open("wb") as file:
            while chunk := response.read(1024 * 1024):
                file.write(chunk)
        if temp_path.stat().st_size <= 0:
            raise RuntimeError(f"图片下载为空：{normalized_url}")
        temp_path.replace(final_path)
        return final_path


def _image_stem(images_dir: Path, index: int, url: str) -> Path:
    digest = hashlib.sha256(_absolute_image_url(url).encode("utf-8")).hexdigest()[:12]
    return images_dir / f"{index:03d}_{digest}"


def localize_content_images(
    content_html: str,
    article_dir: Path,
    *,
    image_loader: Callable[[str, Path], Path] = _download_remote_image,
) -> tuple[str, int]:
    soup = BeautifulSoup(content_html or "", "html.parser")
    images_dir = article_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    cache: dict[str, str] = {}

    def localize(url: str) -> str:
        normalized = _absolute_image_url(url)
        if normalized.startswith("blob:"):
            return ""
        if not normalized or normalized.startswith(("data:", "images/", "cover.")):
            return normalized
        if normalized in cache:
            return cache[normalized]
        local_path = image_loader(normalized, _image_stem(images_dir, len(cache) + 1, normalized))
        relative = local_path.relative_to(article_dir).as_posix()
        cache[normalized] = relative
        return relative

    for image in soup.find_all("img"):
        candidates = [image.get("data-original"), image.get("data-src"), image.get("src")]
        source = next(
            (str(value) for value in candidates if value and not str(value).startswith("blob:")),
            "",
        )
        if source:
            image["src"] = localize(str(source))
        elif str(image.get("src") or "").startswith("blob:"):
            image.attrs.pop("src", None)
        for attribute in ("data-src", "data-original", "srcset"):
            image.attrs.pop(attribute, None)

    for source_tag in soup.find_all("source"):
        source = source_tag.get("src")
        if source:
            source_tag["src"] = localize(str(source))
        srcset = source_tag.get("srcset")
        if srcset:
            first_url = str(srcset).split(",", 1)[0].strip().split(" ", 1)[0]
            source_tag["srcset"] = localize(first_url)

    for video in soup.find_all("video"):
        poster = video.get("poster")
        if poster:
            video["poster"] = localize(str(poster))

    def replace_style_url(match: re.Match[str]) -> str:
        source = match.group(2).strip()
        return f"url('{localize(source)}')"

    for node in soup.find_all(style=True):
        style = str(node.get("style") or "")
        node["style"] = STYLE_URL_PATTERN.sub(replace_style_url, style)

    for style_tag in soup.find_all("style"):
        css = style_tag.string or style_tag.get_text()
        if css:
            style_tag.string = STYLE_URL_PATTERN.sub(replace_style_url, css)

    return str(soup), len(cache)


def _assert_no_external_images(content_html: str) -> None:
    soup = BeautifulSoup(content_html, "html.parser")
    for tag in soup.find_all(["img", "source"]):
        for attribute in ("src", "srcset", "data-src", "data-original"):
            value = str(tag.get(attribute) or "")
            if (
                "http://" in value
                or "https://" in value
                or value.startswith(("//", "blob:"))
            ):
                raise RuntimeError(f"离线 HTML 仍含外链图片：{value[:120]}")
    for node in soup.find_all(style=True):
        if re.search(r"url\(['\"]?(?:https?:)?//", str(node.get("style") or ""), re.I):
            raise RuntimeError("离线 HTML 样式中仍含外链图片")
    for video in soup.find_all("video"):
        poster = str(video.get("poster") or "")
        if poster.startswith(("http://", "https://", "//")):
            raise RuntimeError(f"离线 HTML 仍含外链视频封面：{poster[:120]}")
    for style_tag in soup.find_all("style"):
        if re.search(r"url\(['\"]?(?:https?:)?//", style_tag.get_text(), re.I):
            raise RuntimeError("离线 HTML 样式表中仍含外链图片")


def archive_text_article(
    *,
    title: str,
    published_at: str,
    content_html: str,
    cover_url: str,
    source_url: str,
    output_dir: Path,
    image_loader: Callable[[str, Path], Path] = _download_remote_image,
) -> dict[str, object]:
    article_dir = build_text_archive_dir(output_dir, title, published_at)
    article_dir.mkdir(parents=True, exist_ok=True)
    index_path = article_dir / "index.html"
    if index_path.exists():
        saved = index_path.read_text(encoding="utf-8")
        _assert_no_external_images(saved)
        return {"status": "skipped_existing", "path": str(index_path)}

    cover_relative = ""
    if cover_url:
        cover_path = image_loader(_absolute_image_url(cover_url), article_dir / "cover")
        cover_relative = cover_path.relative_to(article_dir).as_posix()
    localized_html, image_count = localize_content_images(
        content_html, article_dir, image_loader=image_loader
    )
    cover_html = (
        f'<img class="cover" src="{html.escape(cover_relative, quote=True)}" alt="封面">'
        if cover_relative
        else ""
    )
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body {{ max-width: 860px; margin: 32px auto; padding: 0 20px; color: #222; line-height: 1.75; font-family: system-ui, sans-serif; }}
    h1 {{ line-height: 1.35; }} .meta {{ color: #777; margin-bottom: 24px; }}
    .cover {{ display: block; max-width: 100%; max-height: 520px; margin: 0 auto 28px; object-fit: contain; }}
    article img {{ max-width: 100%; height: auto; }} table {{ max-width: 100%; border-collapse: collapse; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="meta">上架时间：{html.escape(published_at)} · <a href="{html.escape(source_url, quote=True)}">原始页面</a></div>
  {cover_html}
  <article>{localized_html}</article>
</body>
</html>
"""
    _assert_no_external_images(document)
    temp_path = index_path.with_suffix(".html.tmp")
    temp_path.write_text(document, encoding="utf-8")
    temp_path.replace(index_path)
    return {
        "status": "downloaded",
        "path": str(index_path),
        "body_image_count": image_count,
        "has_cover": bool(cover_relative),
    }
