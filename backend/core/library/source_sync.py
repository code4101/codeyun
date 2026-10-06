"""Conservative, chapter-level FDE synchronization; local IDs and edits are authoritative.

The first run establishes a baseline only where the imported text matches upstream.
Subsequent runs use three-way comparison. Deleted pages and ambiguous annotations
keep their old chapter, rather than silently destroying a reader's work.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import html
import re
from urllib.parse import urljoin, urlsplit
import urllib.request

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

ORIGIN = "https://fde-wiki.github.io"
PAGE_PATH = re.compile(r"/(?:part\d+|ch\d+|topic\d+|appendix)$")
HEADING = re.compile(r'<h1\b[^>]*\bdata-toc-anchor=["\']([^"\']+)["\'][^>]*>.*?</h1>', re.S | re.I)


def source_url(value: str) -> str | None:
    parsed = urlsplit(urljoin(ORIGIN, value))
    path = re.sub(r"\.(?:md|html)$", "", parsed.path).rstrip("/")
    if parsed.netloc == "fde-wiki.github.io" and parsed.scheme == "https" and PAGE_PATH.fullmatch(path):
        return ORIGIN + path
    return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def semantic_html(text: str) -> str:
    """Ignore prose spacing, but retain code indentation and image identity."""
    soup = BeautifulSoup(text, "html.parser")
    code = [node.get_text().replace("\r\n", "\n").strip("\n") for node in soup.select("pre")]
    images = [str(node.get("src", "")) for node in soup.select("img")]
    for node in soup.select("pre, hr, script, style"):
        node.decompose()
    return _hash(re.sub(r"\s+", "", soup.get_text()) + repr(code) + repr(images))


def quote_offset(text: str, quote: str, prefix: str = "", suffix: str = "") -> int:
    """Locate a unique quote/context; never guess the first duplicate occurrence."""
    if not quote:
        return -1
    positions = [match.start() for match in re.finditer('(?=' + re.escape(quote) + ')', text)]
    contextual = [i for i in positions if (not prefix or text[:i].endswith(prefix))
                  and (not suffix or text[i + len(quote):].startswith(suffix))]
    if len(contextual) == 1:
        return contextual[0]
    return positions[0] if len(positions) == 1 else -1


@dataclass
class Chapter:
    anchor: str
    url: str
    prefix: str
    body: str
    start: int
    end: int


def chapters(content: str) -> list[Chapter]:
    boundaries = list(HEADING.finditer(content))
    result = []
    for index, match in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(content)
        section = content[match.end():end]
        # Source metadata and the local snapshot notice are not upstream prose.
        source = re.search(r'<p\b[^>]*>\s*来源.*?</p>', section, re.S)
        if not source:
            continue
        link = BeautifulSoup(source.group(), "html.parser").find("a")
        url = source_url(str(link.get("href", ""))) if link else None
        if not url:
            continue
        body_start = source.end()
        notice = re.match(r'\s*<p\b[^>]*>全站正文离线快照.*?</p>', section[body_start:], re.S)
        if notice:
            body_start += notice.end()
        body_end = re.search(r'</body\s*>', section, re.I)
        if body_end:
            end = match.end() + body_end.start()
            section = section[:body_end.start()]
        result.append(Chapter(match[1], url, content[match.start():match.end()] + section[:body_start],
                              section[body_start:], match.start(), end))
    if len({item.url for item in result}) != len(result):
        raise ValueError("同一个原站章节被绑定多次，无法安全同步")
    return result


def render_source(markdown: str, url: str, anchors: dict[str, str]) -> str:
    markdown = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", markdown.replace("\r\n", "\n"), count=1, flags=re.S)
    soup = BeautifulSoup(MarkdownIt("commonmark", {"html": True}).enable("table").render(markdown), "html.parser")
    for heading in soup.select("h1"):
        heading.name = "h2"  # The book's explicit h1 anchors define chapter boundaries.
    for node in soup.select("script, style, iframe, object, embed, form, input, button"):
        node.decompose()
    for node in soup.find_all(True):
        for key in list(node.attrs):
            if key.lower().startswith("on"):
                del node[key]
        for key in ("href", "src"):
            value = node.get(key)
            if not value:
                continue
            resolved = urljoin(url, str(value))
            if urlsplit(resolved).scheme not in {"http", "https"}:
                del node[key]
                continue
            target = source_url(resolved)
            node[key] = "#" + anchors[target] if key == "href" and target in anchors else resolved
    return str(soup)


def _download(url: str) -> str:
    class OfficialRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urlsplit(newurl)
            if parsed.scheme != 'https' or parsed.netloc != 'fde-wiki.github.io':
                raise ValueError("原站重定向到未经允许的地址")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    request = urllib.request.Request(url, headers={"User-Agent": "CodeYun-FDE-Sync/1.0"})
    with urllib.request.build_opener(OfficialRedirect()).open(request, timeout=25) as response:
        if urlsplit(response.url).netloc != "fde-wiki.github.io":
            raise ValueError("原站重定向到未经允许的域名")
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            raise ValueError("原站章节超过大小限制")
        return payload.decode("utf-8-sig")


def fetch_sources(content: str) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Fetch official Markdown and discover additions, with bounded concurrency."""
    catalog_text = _download(ORIGIN + "/llms.txt")
    catalog = {}
    for title, link in re.findall(r'\[([^\]]+)\]\(([^)]+)\)', catalog_text):
        url = source_url(link)
        if url:
            catalog[url] = title
    urls = set(catalog) | {item.url for item in chapters(content)}
    if not urls or len(urls) > 200:
        raise ValueError("原站章节目录为空或超过同步上限")
    pages, errors = {}, {}
    def fetch(url):
        try:
            return url, _download(url + ".md"), None
        except Exception as exc:
            return url, None, str(exc)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for url, page, error in pool.map(fetch, sorted(urls)):
            if error:
                errors[url] = error
            else:
                pages[url] = page
    return pages, catalog, errors


def plan_sync(content: str, pages: dict[str, str], baseline: dict, *, catalog: dict[str, str] | None = None,
              annotations: list[dict] = (), errors: dict[str, str] | None = None) -> dict:
    """Return a safe merge and a persisted baseline; no writes or network here."""
    existing = chapters(content)
    anchors = {chapter.url: chapter.anchor for chapter in existing}
    for url in pages:
        if url not in anchors:
            anchors[url] = "source-" + _hash(url)[:16]
    manifest = dict(baseline)
    report = {"updated": [], "added": [], "unchanged": [], "conflicts": [], "errors": errors or {}}
    replacements = []
    for chapter in existing:
        if chapter.url not in pages:
            report["conflicts"].append({"chapter_id": chapter.anchor, "reason": "原站不可读取，保留本地章节"})
            continue
        remote = render_source(pages[chapter.url], chapter.url, anchors)
        remote_hash = _hash(pages[chapter.url])
        local_hash = _hash(chapter.body)
        previous = baseline.get(chapter.url)
        equal = semantic_html(chapter.body) == semantic_html(remote)
        safe = previous and previous.get("local_hash") == local_hash
        if equal and (not previous or previous.get("remote_hash") == remote_hash):
            manifest[chapter.url] = {"chapter_id": chapter.anchor, "remote_hash": remote_hash, "local_hash": local_hash}
            report["unchanged"].append(chapter.anchor)
            continue
        if previous and previous.get("remote_hash") == remote_hash:
            report["unchanged"].append(chapter.anchor)  # keep local edits
            continue
        if not safe:
            report["conflicts"].append({"chapter_id": chapter.anchor, "reason": "本地与原站均有差异，保留本地内容"})
            continue
        # Match the same DOM text stream used by RichTextDocumentReader.
        new_text = BeautifulSoup(chapter.prefix + remote, "html.parser").get_text()
        affected = [a for a in annotations if a.get("chapter_id") == chapter.anchor and a.get("quote_text")]
        if any(quote_offset(new_text, a["quote_text"], a.get("prefix_text", ""), a.get("suffix_text", "")) < 0 for a in affected):
            report["conflicts"].append({"chapter_id": chapter.anchor, "reason": "高亮原文无法唯一定位，保留旧章节"})
            continue
        replacements.append((chapter.start, chapter.end, chapter.prefix + remote))
        manifest[chapter.url] = {"chapter_id": chapter.anchor, "remote_hash": remote_hash, "local_hash": _hash(remote)}
        report["updated"].append(chapter.anchor)
    for start, end, replacement in sorted(replacements, reverse=True):
        content = content[:start] + replacement + content[end:]
    added = []
    for url, title in (catalog or {}).items():
        if url in {c.url for c in existing} or url not in pages:
            continue
        anchor = anchors[url]
        body = render_source(pages[url], url, anchors)
        added.append(f'<h1 data-toc-anchor="{anchor}" data-toc-level="1">{html.escape(title)}</h1>'
                     f'<p>来源：<a href="{url}">FDE-Wiki 原文</a></p>{body}')
        manifest[url] = {"chapter_id": anchor, "remote_hash": _hash(pages[url]), "local_hash": _hash(body)}
        report["added"].append(anchor)
    if added:
        closing = re.search(r'</body\s*>', content, re.I)
        index = closing.start() if closing else len(content)
        content = content[:index] + "".join(added) + content[index:]
    return {**report, "content": content, "baseline": manifest}
