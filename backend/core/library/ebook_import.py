from __future__ import annotations

import hashlib
import html
import mimetypes
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag

from backend.core.library.linux_do_book import LinuxDoTocItem


SUPPORTED_EBOOK_SUFFIXES = {".epub", ".html", ".htm", ".md", ".markdown", ".txt"}


class EbookImportError(ValueError):
    pass


@dataclass(slots=True)
class ImportedEbook:
    format: str
    title: str
    author: str
    content_html: str
    content_text: str
    toc: list[LinuxDoTocItem]
    revision: str
    resources: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    cover_resource_name: str = ""


def supported_ebook_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_EBOOK_SUFFIXES


def import_ebook(path: Path, *, book_id: str, filename: str | None = None) -> ImportedEbook:
    source_name = filename or path.name
    suffix = Path(source_name).suffix.lower()
    if suffix == ".epub":
        return _import_epub(path, book_id=book_id, source_name=source_name)
    if suffix in {".html", ".htm"}:
        return _import_html(path, source_name)
    if suffix in {".md", ".markdown", ".txt"}:
        return _import_plain_text(path, source_name, suffix)
    raise EbookImportError("支持 EPUB、HTML、Markdown 和 TXT 电子书")


def _decode_text(payload: bytes) -> str:
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _safe_title(value: str, fallback: str) -> str:
    normalized = re.sub(r"\s+", " ", value or "").strip()
    return (normalized or fallback).strip()[:240]


def _sanitize_fragment(fragment: BeautifulSoup | Tag) -> None:
    for node in fragment.select("script, style, iframe, object, embed, form, link, base"):
        node.decompose()
    for node in fragment.find_all(True):
        for attribute in list(node.attrs):
            if attribute.lower().startswith("on"):
                node.attrs.pop(attribute, None)
        for attribute in ("href", "src"):
            value = str(node.get(attribute) or "")
            if re.match(r"^\s*javascript:", value, re.IGNORECASE):
                node.attrs.pop(attribute, None)


def _revision(content_html: str) -> str:
    return hashlib.sha256(content_html.encode("utf-8")).hexdigest()


def _single_article(title: str, body_html: str, *, format_name: str) -> ImportedEbook:
    article_id = "article-1"
    content_html = f'<article data-article-id="{article_id}"><h1 id="{article_id}">{html.escape(title)}</h1>{body_html}</article>'
    text = BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
    return ImportedEbook(
        format=format_name,
        title=title,
        author="",
        content_html=content_html,
        content_text=text,
        toc=[LinuxDoTocItem(title=title, number="", level=1, anchor=article_id)],
        revision=_revision(content_html),
    )


def _unwrap_legacy_layout_tables(body: BeautifulSoup | Tag) -> None:
    """Remove conservative one-cell table wrappers used by old page layouts."""
    for table in reversed(list(body.find_all("table"))):
        rows = [
            row
            for row in table.find_all("tr")
            if row.find_parent("table") is table
        ]
        cells = [
            cell
            for row in rows
            for cell in row.find_all(["td", "th"], recursive=False)
        ]
        if (
            len(rows) != 1
            or len(cells) != 1
            or cells[0].name != "td"
            or table.find("caption") is not None
        ):
            continue
        cell = cells[0]
        for child in list(cell.contents):
            table.insert_before(child.extract())
        table.decompose()


_NUMBERED_SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"[（(](?:\d{1,3}|[一二三四五六七八九十百]{1,5})[）)]\s*\S"
    r"|(?:\d{1,3}|[一二三四五六七八九十百]{1,5})\s*[、.．]\s*\S"
    r"|(?:\d{1,3}|[一二三四五六七八九十百]{1,5})\s+\S"
    r"|第(?:\d{1,3}|[一二三四五六七八九十百]{1,5})[章节编部篇卷](?:\s+|$)"
    r")"
)

_PLAIN_SECTION_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:零|〇|[一二三四五六七八九十百]{1,6}|\d{1,3})"
    r"(?:\s*[、.．。]\s*|\s+)\S.{0,98}"
    r"|第(?:\d{1,3}|[一二三四五六七八九十百]{1,6})[章节编部篇卷]\s*\S.{0,98}"
    r")$"
)


def _section_title_key(value: str) -> str:
    title = re.sub(r"\s+", " ", value or "").strip()
    if not 2 <= len(title) <= 100 or not _PLAIN_SECTION_HEADING_RE.fullmatch(title):
        return ""
    return re.sub(r"[\s、.．。:：]+", "", title)


def _promote_directory_section_headings(body: BeautifulSoup | Tag) -> int:
    """Turn a plain-text contents list plus repeated body labels into headings."""
    nodes = [node for node in body.children if isinstance(node, Tag)]
    directory_index = next((
        index
        for index, node in enumerate(nodes)
        if re.sub(r"\s+", "", node.get_text(" ", strip=True)) in {"目录", "目次"}
    ), None)
    if directory_index is None:
        return 0

    catalog: dict[str, str] = {}
    first_body_heading_index: int | None = None
    for index in range(directory_index + 1, len(nodes)):
        title = re.sub(r"\s+", " ", nodes[index].get_text(" ", strip=True)).strip()
        key = _section_title_key(title)
        if not key:
            if catalog:
                break
            continue
        if key in catalog:
            first_body_heading_index = index
            break
        catalog[key] = title

    if len(catalog) < 2 or first_body_heading_index is None:
        return 0

    for node in nodes[directory_index:first_body_heading_index]:
        node.decompose()

    promoted = 0
    for node in nodes[first_body_heading_index:]:
        key = _section_title_key(node.get_text(" ", strip=True))
        canonical_title = catalog.get(key)
        if not canonical_title:
            continue
        node.name = "h1"
        node.clear()
        node.append(canonical_title)
        promoted += 1
    return promoted


def _looks_like_generated_document_title(value: str) -> bool:
    normalized = re.sub(r"\s+", "", value or "")
    return (
        5 <= len(normalized) <= 40
        and normalized.isascii()
        and normalized.isalnum()
        and any(character.isalpha() for character in normalized)
        and any(character.isdigit() for character in normalized)
    )


def _is_cover_only_body(body: BeautifulSoup | Tag) -> bool:
    return not body.get_text(" ", strip=True) and body.find(["img", "image", "svg"]) is not None


def _promote_numbered_emphasis_headings(body: BeautifulSoup | Tag) -> None:
    """Restore section headings encoded as standalone bold paragraphs.

    Older HTML books often express real numbered section titles with a bare
    ``<b>``/``<strong>`` element instead of a heading tag.  Only standalone,
    short, explicitly numbered emphasis is promoted so ordinary inline bold
    text remains untouched.
    """
    candidates: list[tuple[Tag, Tag, int]] = []
    heading_names = ["h1", "h2", "h3", "h4", "h5", "h6"]
    for emphasis in body.find_all(["b", "strong"]):
        container = emphasis
        parent = emphasis.parent
        if isinstance(parent, Tag) and parent.name == "p":
            if parent.get_text(" ", strip=True) != emphasis.get_text(" ", strip=True):
                continue
            container = parent
        if container.parent is not body:
            continue
        title = re.sub(r"\s+", " ", container.get_text(" ", strip=True)).strip()
        if not 3 <= len(title) <= 120 or not _NUMBERED_SECTION_HEADING_RE.match(title):
            continue
        previous_heading = container.find_previous(heading_names)
        previous_level = (
            int(previous_heading.name[1])
            if isinstance(previous_heading, Tag) and previous_heading.name
            else 1
        )
        candidates.append((emphasis, container, min(previous_level + 1, 6)))

    for emphasis, container, level in candidates:
        container.name = f"h{level}"
        if container is not emphasis:
            emphasis.unwrap()


def _chapter_heading_level(body: BeautifulSoup | Tag) -> int | None:
    counts: dict[int, int] = {}
    for node in body.children:
        if not isinstance(node, Tag) or not re.fullmatch(r"h[1-4]", node.name or ""):
            continue
        level = int(node.name[1])
        counts[level] = counts.get(level, 0) + 1
    return next((level for level in range(1, 5) if counts.get(level, 0) >= 2), None)


def _leading_nodes_are_navigation(nodes: list[object]) -> bool:
    tags = [node for node in nodes if isinstance(node, Tag)]
    text = re.sub(
        r"\s+",
        " ",
        " ".join(tag.get_text(" ", strip=True) for tag in tags),
    ).strip()
    link_count = sum(len(tag.find_all("a")) + int(tag.name == "a") for tag in tags)
    has_separator = any(tag.name == "hr" or tag.find("hr") is not None for tag in tags)
    return len(text) <= 600 and link_count >= 2 and has_separator


def _multi_article_html_book(
    body: BeautifulSoup | Tag,
    *,
    title: str,
) -> ImportedEbook | None:
    chapter_level = _chapter_heading_level(body)
    if chapter_level is None:
        return None

    nodes = list(body.children)
    boundary_indexes = [
        index
        for index, node in enumerate(nodes)
        if isinstance(node, Tag) and node.name == f"h{chapter_level}"
    ]
    if len(boundary_indexes) < 2:
        return None

    leading_nodes = nodes[:boundary_indexes[0]]
    keep_leading_nodes = not _leading_nodes_are_navigation(leading_nodes)
    articles: list[str] = []
    toc: list[LinuxDoTocItem] = []
    for article_index, start in enumerate(boundary_indexes, start=1):
        end = boundary_indexes[article_index] if article_index < len(boundary_indexes) else len(nodes)
        heading = nodes[start]
        if not isinstance(heading, Tag):
            continue
        anchor = f"article-{article_index}"
        heading["id"] = anchor
        article_nodes = nodes[start:end]
        if article_index == 1 and keep_leading_nodes:
            article_nodes = [*leading_nodes, *article_nodes]
        chapter_title = _safe_title(heading.get_text(" ", strip=True), f"第 {article_index} 章")
        article_html = "".join(map(str, article_nodes)).strip()
        articles.append(f'<article data-article-id="{anchor}">{article_html}</article>')
        toc.append(LinuxDoTocItem(
            title=chapter_title,
            number="",
            level=1,
            anchor=anchor,
        ))

    if len(articles) < 2:
        return None
    content_html = "".join(articles)
    content_text = BeautifulSoup(content_html, "html.parser").get_text(" ", strip=True)
    return ImportedEbook(
        format="html",
        title=title,
        author="",
        content_html=content_html,
        content_text=content_text,
        toc=toc,
        revision=_revision(content_html),
    )


def _import_html(path: Path, source_name: str) -> ImportedEbook:
    soup = BeautifulSoup(_decode_text(path.read_bytes()), "html.parser")
    title_node = soup.find("title") or soup.find(["h1", "h2"])
    title = _safe_title(title_node.get_text(" ", strip=True) if isinstance(title_node, Tag) else "", path.stem)
    body = soup.body or soup
    _sanitize_fragment(body)
    _unwrap_legacy_layout_tables(body)
    _promote_numbered_emphasis_headings(body)
    multi_article = _multi_article_html_book(body, title=title)
    if multi_article is not None:
        return multi_article
    return _single_article(title, "".join(map(str, body.contents)), format_name="html")


def _import_plain_text(path: Path, source_name: str, suffix: str) -> ImportedEbook:
    text = _decode_text(path.read_bytes()).strip()
    title = _safe_title(path.stem, "未命名电子书")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    body_html = "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in paragraphs)
    return _single_article(title, body_html, format_name="markdown" if suffix != ".txt" else "text")


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_xml_text(root: ElementTree.Element, local_name: str) -> str:
    for node in root.iter():
        if _xml_local_name(node.tag) == local_name and (node.text or "").strip():
            return (node.text or "").strip()
    return ""


def _normalize_zip_path(path: str) -> str:
    normalized = posixpath.normpath(unquote(path).replace("\\", "/")).lstrip("/")
    if normalized == ".." or normalized.startswith("../"):
        raise EbookImportError("EPUB 中包含不安全的资源路径")
    return normalized


def _joined_zip_path(base_path: str, relative_path: str) -> str:
    return _normalize_zip_path(posixpath.join(posixpath.dirname(base_path), relative_path))


def _author_from_filename(source_name: str, title: str) -> str:
    candidates = re.findall(r"[（(]([^()（）]{1,80})[）)]", Path(source_name).stem)
    for candidate in candidates:
        normalized = re.sub(r"\s+", " ", candidate).strip()
        lowered = normalized.lower()
        if (
            not normalized
            or normalized == title
            or any(marker in lowered for marker in ("z-library", "z-lib", "1lib", "isbn", ".com", ".sk"))
            or re.search(r"(?:第\s*\d+\s*版|出版社|出版|epub|pdf)", normalized, re.IGNORECASE)
        ):
            continue
        return _safe_title(normalized, "")
    return ""


def _import_epub(path: Path, *, book_id: str, source_name: str) -> ImportedEbook:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise EbookImportError("文件不是有效的 EPUB") from exc
    with archive:
        names = set(archive.namelist())
        try:
            if archive.read("mimetype").strip() != b"application/epub+zip":
                raise EbookImportError("文件不是有效的 EPUB")
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(
                str(node.attrib.get("full-path") or "")
                for node in container.iter()
                if _xml_local_name(node.tag) == "rootfile" and node.attrib.get("full-path")
            )
            opf_path = _normalize_zip_path(rootfile)
            opf = ElementTree.fromstring(archive.read(opf_path))
        except (KeyError, StopIteration, ElementTree.ParseError) as exc:
            raise EbookImportError("EPUB 缺少有效的书籍清单") from exc

        title = _safe_title(_first_xml_text(opf, "title"), path.stem)
        author = _safe_title(_first_xml_text(opf, "creator"), "") if _first_xml_text(opf, "creator") else ""
        if author.lower() in {"", "未知", "unknown"}:
            author = _author_from_filename(source_name, title) or author
        manifest: dict[str, dict[str, str]] = {}
        spine_ids: list[str] = []
        cover_id = ""
        for node in opf.iter():
            local_name = _xml_local_name(node.tag)
            if local_name == "item" and node.attrib.get("id") and node.attrib.get("href"):
                manifest[str(node.attrib["id"])] = {
                    "href": str(node.attrib["href"]),
                    "media_type": str(node.attrib.get("media-type") or ""),
                    "properties": str(node.attrib.get("properties") or ""),
                }
            elif local_name == "itemref" and node.attrib.get("idref"):
                spine_ids.append(str(node.attrib["idref"]))
            elif local_name == "meta" and str(node.attrib.get("name") or "").lower() == "cover":
                cover_id = str(node.attrib.get("content") or "")

        spine_paths = [
            _joined_zip_path(opf_path, manifest[item_id]["href"])
            for item_id in spine_ids
            if item_id in manifest
        ]
        if not spine_paths:
            raise EbookImportError("EPUB 没有可阅读的正文")
        chapter_anchors = {chapter_path: f"article-{index}" for index, chapter_path in enumerate(spine_paths, start=1)}
        resources: dict[str, tuple[bytes, str]] = {}

        def resource_url(chapter_path: str, raw_url: str) -> str:
            split = urlsplit(raw_url)
            if split.scheme or raw_url.startswith(("//", "data:")):
                return raw_url
            resolved = _joined_zip_path(chapter_path, split.path)
            if resolved in chapter_anchors:
                return f"#{chapter_anchors[resolved]}"
            if resolved not in names:
                return raw_url
            payload = archive.read(resolved)
            extension = Path(resolved).suffix.lower()
            resource_name = f"{hashlib.sha256(resolved.encode()).hexdigest()[:20]}{extension}"
            media_type = mimetypes.guess_type(resolved)[0] or "application/octet-stream"
            resources[resource_name] = (payload, media_type)
            return f"/api/linux-do-books/{quote(book_id, safe='')}/resources/{quote(resource_name, safe='')}"

        cover_item = next(
            (item for item in manifest.values() if "cover-image" in item["properties"].split()),
            manifest.get(cover_id),
        )
        cover_resource_name = ""
        if cover_item:
            cover_path = _joined_zip_path(opf_path, cover_item["href"])
            if cover_path in names:
                extension = Path(cover_path).suffix.lower()
                cover_resource_name = f"cover{extension}"
                resources[cover_resource_name] = (
                    archive.read(cover_path),
                    cover_item["media_type"] or mimetypes.guess_type(cover_path)[0] or "application/octet-stream",
                )

        processed_spine: list[tuple[int, str, str, str]] = []
        for index, chapter_path in enumerate(spine_paths, start=1):
            if chapter_path not in names:
                continue
            soup = BeautifulSoup(_decode_text(archive.read(chapter_path)), "html.parser")
            body = soup.body or soup
            _sanitize_fragment(body)
            _promote_numbered_emphasis_headings(body)
            for node in body.find_all(True):
                for attribute in ("src", "href", "xlink:href"):
                    raw_value = str(node.get(attribute) or "").strip()
                    if raw_value and not raw_value.startswith("#"):
                        node[attribute] = resource_url(chapter_path, raw_value)
            title_node = soup.find("title")
            document_title = (
                title_node.get_text(" ", strip=True)
                if isinstance(title_node, Tag)
                else ""
            )
            if _looks_like_generated_document_title(document_title):
                document_title = ""
            if _is_cover_only_body(body):
                continue
            processed_spine.append((
                index,
                chapter_path,
                document_title,
                "".join(map(str, body.contents)),
            ))

        combined = BeautifulSoup("<body></body>", "html.parser")
        combined_body = combined.body
        if combined_body is not None:
            for _index, _chapter_path, _document_title, body_html in processed_spine:
                fragment = BeautifulSoup(body_html, "html.parser")
                for node in list(fragment.contents):
                    combined_body.append(node)
            if _promote_directory_section_headings(combined_body) >= 2:
                inferred = _multi_article_html_book(combined_body, title=title)
                if inferred is not None:
                    inferred.format = "epub"
                    inferred.author = author
                    inferred.resources = resources
                    inferred.cover_resource_name = cover_resource_name
                    return inferred

        articles: list[str] = []
        toc: list[LinuxDoTocItem] = []
        text_parts: list[str] = []
        for index, chapter_path, document_title, body_html in processed_spine:
            soup = BeautifulSoup(body_html, "html.parser")
            body = soup.body or soup
            heading = body.find(["h1", "h2", "h3"])
            chapter_title = _safe_title(
                heading.get_text(" ", strip=True) if isinstance(heading, Tag)
                else document_title,
                f"第 {index} 章",
            )
            anchor = chapter_anchors[chapter_path]
            if isinstance(heading, Tag):
                heading["id"] = anchor
                body_html = "".join(map(str, body.contents))
            else:
                body_html = f'<h1 id="{anchor}">{html.escape(chapter_title)}</h1>' + "".join(map(str, body.contents))
            articles.append(f'<article data-article-id="{anchor}">{body_html}</article>')
            chapter_text = body.get_text(" ", strip=True)
            text_parts.append(chapter_text)
            toc.append(LinuxDoTocItem(title=chapter_title, number="", level=1, anchor=anchor))

        content_html = "".join(articles)
        if not content_html:
            raise EbookImportError("EPUB 正文章节无法读取")

        return ImportedEbook(
            format="epub",
            title=title,
            author=author,
            content_html=content_html,
            content_text="\n".join(text_parts),
            toc=toc,
            revision=_revision(content_html),
            resources=resources,
            cover_resource_name=cover_resource_name,
        )
