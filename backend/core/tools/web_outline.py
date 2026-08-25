from __future__ import annotations

import ipaddress
import math
import re
import socket
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag, UnicodeDammit


MAX_HTML_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 4
FETCH_TIMEOUT_SECONDS = (5, 15)
USER_AGENT = "CodeYun-WebOutline/1.0 (+local content analysis tool)"
_PROXY_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_SPACE_RE = re.compile(r"\s+")
_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:(?:第\s*[一二三四五六七八九十百千万零〇两\d]+\s*(?:部分|章节|章|节|篇|部|卷))|"
    r"(?:\(?[一二三四五六七八九十百千万零〇两]+[、.．)]\s*)|"
    r"(?:\(?\d+(?:\.\d+)*[、.．):：]?\s*))"
)


class WebOutlineError(RuntimeError):
    """Raised when a page cannot be safely fetched or converted to an outline."""


@dataclass(frozen=True)
class SourceHeading:
    source_index: int
    title: str
    html_level: int
    context: str = ""


def _clean_text(value: str | None) -> str:
    return _SPACE_RE.sub(" ", value or "").strip()


def _decode_html(raw: bytes, content_type: str, fallback_encoding: str | None = None) -> str:
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, flags=re.IGNORECASE)
    if not charset_match:
        detected = UnicodeDammit(
            raw,
            ["utf-8", fallback_encoding] if fallback_encoding else ["utf-8"],
            is_html=True,
        ).unicode_markup
        if detected is not None:
            return detected
    encoding = charset_match.group(1) if charset_match else (fallback_encoding or "utf-8")
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(ip.is_global)


def _is_proxy_fake_ip(value: str) -> bool:
    try:
        return ipaddress.ip_address(value.split("%", 1)[0]) in _PROXY_FAKE_IP_NETWORK
    except ValueError:
        return False


def normalize_article_url(url: str) -> str:
    """Normalize transport-irrelevant URL parts without guessing site routes."""
    parsed = urlsplit((url or "").strip())
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _canonical_article_url(html: str, base_url: str) -> str:
    """Read the standard canonical URL declared by a successfully fetched page."""
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one("link[rel~='canonical'][href]")
    if not isinstance(canonical, Tag):
        return base_url
    candidate = urljoin(base_url, str(canonical.get("href") or "").strip())
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return base_url
    return _strip_pagination(normalize_article_url(candidate))


def _strip_pagination(url: str) -> str:
    """Collapse common canonical pagination forms to the document root."""
    parsed = urlsplit(url)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.lower() in {"page", "paged", "page_number", "pagenum"} and value.isdigit())
    ]
    path = re.sub(r"/page/\d+/?$", "/", parsed.path, flags=re.IGNORECASE)
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(filtered_query, doseq=True), ""))


def validate_public_url(
    url: str,
    *,
    resolver: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
) -> str:
    normalized = (url or "").strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise WebOutlineError("只支持 http:// 或 https:// 网页地址")
    if not parsed.hostname or parsed.username or parsed.password:
        raise WebOutlineError("网页地址格式无效")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(_LOCAL_HOST_SUFFIXES):
        raise WebOutlineError("出于安全考虑，不能访问本机、内网或非公网地址")
    try:
        literal_host = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        literal_host = None
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise WebOutlineError("网页端口无效") from exc
    try:
        addresses = {
            item[4][0]
            for item in resolver(hostname, port, type=socket.SOCK_STREAM)
            if item and len(item) > 4 and item[4]
        }
    except OSError as exc:
        raise WebOutlineError(f"无法解析网页域名：{hostname}") from exc
    # Clash 等透明代理会把公网域名解析到 RFC 2544 的 fake-IP 网段。仅域名解析结果
    # 可以接受该网段；用户直接输入这一保留地址时仍拒绝。
    allow_proxy_fake_ip = literal_host is None and addresses and all(_is_proxy_fake_ip(address) for address in addresses)
    if not addresses or (not allow_proxy_fake_ip and any(not _is_public_ip(address) for address in addresses)):
        raise WebOutlineError("出于安全考虑，不能访问本机、内网或非公网地址")
    return normalized


def fetch_public_html(url: str) -> tuple[str, str, str]:
    """Fetch a public page, following its standard canonical article URL once."""
    requested_url = normalize_article_url(url)
    html, final_url, content_type = _fetch_public_html_once(requested_url)
    canonical_url = _canonical_article_url(html, final_url)
    if canonical_url != normalize_article_url(final_url):
        validate_public_url(canonical_url)
        return _fetch_public_html_once(canonical_url)
    return html, final_url, content_type


def _fetch_public_html_once(url: str) -> tuple[str, str, str]:
    session = requests.Session()
    session.trust_env = False
    current_url = url
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            current_url = validate_public_url(current_url)
            try:
                response = session.get(
                    current_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5",
                    },
                    timeout=FETCH_TIMEOUT_SECONDS,
                    allow_redirects=False,
                    stream=True,
                )
            except requests.RequestException:
                # TLS interception, transparent proxies, and protected sites
                # may reject the plain requests fingerprint before returning
                # an HTTP status.  Treat that the same as a protected response
                # and continue through the browser-compatible fetcher.
                return _fetch_public_html_impersonated(current_url)

            if response.status_code in {403, 429, 503}:
                response.close()
                return _fetch_public_html_impersonated(current_url)

            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise WebOutlineError("网页返回了无目标地址的重定向")
                if redirect_count >= MAX_REDIRECTS:
                    raise WebOutlineError("网页重定向次数过多")
                current_url = urljoin(current_url, location)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                status_code = response.status_code
                response.close()
                raise WebOutlineError(f"网页返回 HTTP {status_code}") from exc

            content_type = (response.headers.get("Content-Type") or "").lower()
            if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                response.close()
                raise WebOutlineError("该地址返回的不是 HTML 或纯文本网页")

            declared_size = response.headers.get("Content-Length")
            if declared_size and declared_size.isdigit() and int(declared_size) > MAX_HTML_BYTES:
                response.close()
                raise WebOutlineError("网页内容超过 2 MB，已停止读取")

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_HTML_BYTES:
                    response.close()
                    raise WebOutlineError("网页内容超过 2 MB，已停止读取")
                chunks.append(chunk)
            raw = b"".join(chunks)
            final_url = response.url or current_url
            response.close()
            return _decode_html(raw, content_type, response.encoding), final_url, content_type
    finally:
        session.close()
    raise WebOutlineError("无法完成网页抓取")


def _fetch_public_html_impersonated(url: str) -> tuple[str, str, str]:
    """Retry protected public pages with a modern browser TLS fingerprint."""
    from curl_cffi import requests as curl_requests

    last_status: int | None = None
    last_error: Exception | None = None
    for fingerprint in ("chrome", "safari"):
        current_url = url
        session = curl_requests.Session()
        try:
            for redirect_count in range(MAX_REDIRECTS + 1):
                current_url = validate_public_url(current_url)
                try:
                    response = session.get(
                        current_url,
                        headers={"Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5"},
                        impersonate=fingerprint,
                        timeout=FETCH_TIMEOUT_SECONDS[1],
                        allow_redirects=False,
                    )
                except Exception as exc:
                    last_error = exc
                    break

                if 300 <= response.status_code < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise WebOutlineError("网页返回了无目标地址的重定向")
                    if redirect_count >= MAX_REDIRECTS:
                        raise WebOutlineError("网页重定向次数过多")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code in {403, 429, 503}:
                    last_status = response.status_code
                    break
                if response.status_code >= 400:
                    raise WebOutlineError(f"网页返回 HTTP {response.status_code}")

                content_type = (response.headers.get("Content-Type") or "").lower()
                if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                    raise WebOutlineError("该地址返回的不是 HTML 或纯文本网页")
                raw = bytes(response.content or b"")
                if len(raw) > MAX_HTML_BYTES:
                    raise WebOutlineError("网页内容超过 2 MB，已停止读取")
                return _decode_html(raw, content_type, response.encoding), str(response.url or current_url), content_type
        finally:
            session.close()
    if last_status is not None:
        raise WebOutlineError(f"网页返回 HTTP {last_status}")
    if last_error is not None:
        raise WebOutlineError(f"浏览器兼容抓取失败：{last_error}") from last_error
    raise WebOutlineError("无法完成浏览器兼容抓取")


def _select_content_root(soup: BeautifulSoup) -> Tag:
    heading_selector = "h1, h2, h3, h4, h5, h6"
    selectors = (
        "[itemprop='articleBody']",
        "article",
        ".post-content",
        ".article-content",
        ".entry-content",
        "main",
        "[role='main']",
        ".content",
    )
    candidates: list[Tag] = []
    seen: set[int] = set()

    def add_candidate(node: Tag) -> None:
        identity = id(node)
        if identity in seen:
            return
        text_length = len(_clean_text(node.get_text(" ", strip=True)))
        if text_length < 20 or not node.select(heading_selector):
            return
        seen.add(identity)
        candidates.append(node)

    for selector in selectors:
        for node in soup.select(selector):
            if isinstance(node, Tag):
                add_candidate(node)

    # Framework-neutral fallback: headings reveal likely content containers.
    # Consider their nearby structural ancestors instead of naming a CMS.
    for heading in soup.select(heading_selector):
        ancestor_count = 0
        for parent in heading.parents:
            if not isinstance(parent, Tag):
                continue
            if parent.name in {"article", "section", "main", "div", "noscript", "body"}:
                add_candidate(parent)
                ancestor_count += 1
            if parent.name == "body" or ancestor_count >= 5:
                break

    if candidates:
        metrics: list[tuple[Tag, int, int, float, float, int, int, bool]] = []
        for order, node in enumerate(candidates):
            text = _clean_text(node.get_text(" ", strip=True))
            text_length = max(1, len(text))
            heading_count = len(node.select(heading_selector))
            link_text_length = sum(
                len(_clean_text(link.get_text(" ", strip=True))) for link in node.select("a")
            )
            heading_density = heading_count / math.sqrt(text_length)
            link_density = min(1.0, link_text_length / text_length)
            semantic_score = _content_semantic_score(node)
            metrics.append(
                (
                    node,
                    heading_count,
                    text_length,
                    heading_density,
                    link_density,
                    semantic_score,
                    order,
                    _contains_repeated_content_blocks(node, candidates),
                )
            )

        focused_metrics = [item for item in metrics if not item[7]] or metrics
        maximum_heading_count = max(item[1] for item in focused_metrics)
        minimum_coverage = max(1, math.ceil(maximum_heading_count * 0.75))
        eligible = [item for item in focused_metrics if item[1] >= minimum_coverage]

        def candidate_score(item: tuple[Tag, int, int, float, float, int, int, bool]) -> float:
            _, heading_count, _, heading_density, link_density, semantic_score, order, _ = item
            coverage = heading_count / maximum_heading_count
            return coverage * 100 + heading_density * 200 + semantic_score - link_density * 40 - order * 0.001

        return max(eligible, key=candidate_score)[0]
    return soup.body if isinstance(soup.body, Tag) else soup


def _content_semantic_score(node: Tag) -> int:
    """Score standard article semantics without identifying a particular site."""
    score = 0
    if node.name == "article":
        score += 35
    elif node.name == "main" or str(node.get("role") or "").lower() == "main":
        score += 15
    if str(node.get("itemprop") or "").lower() == "articlebody":
        score += 40
    tokens = " ".join(str(value) for value in (node.get("class") or [])).lower()
    if any(token in tokens for token in ("article", "entry", "post", "content", "story", "document")):
        score += 20
    return score


def _contains_repeated_content_blocks(node: Tag, candidates: list[Tag]) -> bool:
    """Detect list/thread wrappers containing repeated article-like children."""
    signatures: dict[tuple[str, tuple[str, ...]], int] = {}
    for child in candidates:
        if child is node or node not in child.parents or _content_semantic_score(child) < 20:
            continue
        class_tokens = tuple(
            sorted(
                token.lower()
                for token in (child.get("class") or [])
                if any(hint in token.lower() for hint in ("article", "entry", "post", "content", "story", "document"))
            )
        )
        signature = (child.name or "", class_tokens)
        signatures[signature] = signatures.get(signature, 0) + 1
    return any(count >= 2 for count in signatures.values())


def _heading_context(heading: Tag) -> str:
    before_parts: list[str] = []
    for sibling in heading.previous_siblings:
        if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
            break
        if isinstance(sibling, Tag):
            text = _clean_text(sibling.get_text(" ", strip=True))
        else:
            text = _clean_text(str(sibling))
        if text:
            before_parts.append(text)
        if sum(len(part) for part in before_parts) >= 220:
            break

    after_parts: list[str] = []
    for sibling in heading.next_siblings:
        if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
            break
        if isinstance(sibling, Tag):
            text = _clean_text(sibling.get_text(" ", strip=True))
        else:
            text = _clean_text(str(sibling))
        if text:
            after_parts.append(text)
        if sum(len(part) for part in after_parts) >= 220:
            break
    before = _clean_text(" ".join(reversed(before_parts)))[-240:]
    after = _clean_text(" ".join(after_parts))[:240]
    return _clean_text(f"前文：{before} 后文：{after}")


def _readable_heading_nodes(html: str, soup: BeautifulSoup, final_url: str) -> list[Tag]:
    """Match headings retained by a general-purpose main-content extractor."""
    from trafilatura import extract

    try:
        readable_xml = extract(
            html,
            url=final_url,
            output_format="xml",
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception:
        return []
    if not readable_xml:
        return []

    readable_soup = BeautifulSoup(readable_xml, "xml")
    wanted_titles = [
        _clean_text(node.get_text(" ", strip=True))
        for node in readable_soup.select("head")
        if _clean_text(node.get_text(" ", strip=True))
    ]
    if not wanted_titles:
        return []

    raw_nodes = [node for node in soup.select("h1, h2, h3, h4, h5, h6") if isinstance(node, Tag)]
    matched: list[Tag] = []
    used: set[int] = set()
    cursor = 0
    for wanted_title in wanted_titles:
        found_index: int | None = None
        for index in range(cursor, len(raw_nodes)):
            if index not in used and _clean_text(raw_nodes[index].get_text(" ", strip=True)) == wanted_title:
                found_index = index
                break
        if found_index is None:
            for index, node in enumerate(raw_nodes):
                if index not in used and _clean_text(node.get_text(" ", strip=True)) == wanted_title:
                    found_index = index
                    break
        if found_index is not None:
            used.add(found_index)
            cursor = found_index + 1
            matched.append(raw_nodes[found_index])
    return matched


def extract_source_headings(html: str, final_url: str) -> tuple[str, list[SourceHeading]]:
    soup = BeautifulSoup(html, "html.parser")
    # Keep <noscript>: some sites expose crawler-readable article content there.
    for unwanted in soup.select("script, style, template, nav, footer, aside, form, svg"):
        unwanted.decompose()

    meta_title = soup.select_one("meta[property='og:title'], meta[name='twitter:title']")
    document_title = _clean_text(meta_title.get("content") if isinstance(meta_title, Tag) else "")
    if not document_title:
        document_title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    readable_nodes = _readable_heading_nodes(html, soup, final_url)
    root = _select_content_root(soup) if not readable_nodes else None
    heading_nodes = readable_nodes or root.select("h1, h2, h3, h4, h5, h6")
    headings: list[SourceHeading] = []
    seen: set[tuple[int, str]] = set()
    for node in heading_nodes:
        title = _clean_text(node.get_text(" ", strip=True))
        if not title or len(title) > 180:
            continue
        html_level = int(node.name[1])
        dedupe_key = (html_level, title)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        headings.append(
            SourceHeading(
                source_index=len(headings),
                title=title,
                html_level=html_level,
                context=_heading_context(node),
            )
        )

    page_title = next((item.title for item in headings if item.html_level == 1), "") or document_title
    if not page_title:
        page_title = urlsplit(final_url).hostname or final_url
    return page_title, headings


def build_rule_outline(page_title: str, headings: list[SourceHeading]) -> list[dict[str, Any]]:
    if not headings:
        return [{"title": page_title, "level": 1, "source_index": None, "inferred": True}]

    title_source_index = next(
        (item.source_index for item in headings if item.html_level == 1 and item.title == page_title),
        None,
    )
    content_headings = [item for item in headings if item.source_index != title_source_index]
    if not content_headings:
        return [{"title": page_title, "level": 1, "source_index": title_source_index, "inferred": title_source_index is None}]

    outline: list[dict[str, Any]] = [
        {"title": page_title, "level": 1, "source_index": title_source_index, "inferred": title_source_index is None}
    ]

    boundary_positions = [
        index for index, item in enumerate(content_headings) if _boundary_hint(item.context)
    ]
    if not boundary_positions:
        minimum_level = min(item.html_level for item in content_headings)
        for item in content_headings:
            outline.append(
                {
                    "title": item.title,
                    "level": min(6, max(2, item.html_level - minimum_level + 2)),
                    "source_index": item.source_index,
                    "inferred": False,
                }
            )
        return outline

    first_boundary = boundary_positions[0]
    if first_boundary:
        leading = content_headings[:first_boundary]
        leading_minimum = min(item.html_level for item in leading)
        for item in leading:
            outline.append(
                {
                    "title": item.title,
                    "level": min(6, max(2, item.html_level - leading_minimum + 2)),
                    "source_index": item.source_index,
                    "inferred": False,
                }
            )

    for boundary_index, segment_start in enumerate(boundary_positions):
        segment_end = (
            boundary_positions[boundary_index + 1]
            if boundary_index + 1 < len(boundary_positions)
            else len(content_headings)
        )
        segment = content_headings[segment_start:segment_end]
        group_title = _boundary_hint(segment[0].context)
        outline.append(
            {"title": group_title, "level": 2, "source_index": None, "inferred": True}
        )
        minimum_level = min(item.html_level for item in segment)
        for item_index, item in enumerate(segment):
            level = 3 if item_index == 0 else min(6, max(3, item.html_level - minimum_level + 3))
            outline.append(
                {
                    "title": item.title,
                    "level": level,
                    "source_index": item.source_index,
                    "inferred": False,
                }
            )
    return outline


def _boundary_hint(context: str) -> str:
    patterns = (
        r"接下来(?:说说|介绍|讲讲|说|讲|谈)([^：:。；]{1,30})[：:]?",
        r"以下(?:是|介绍)([^：:。；]{1,30})[：:]",
        r"分为以下([^：:。；]{1,30})[：:]",
    )
    for pattern in patterns:
        match = re.search(pattern, context)
        if match:
            return _clean_text(match.group(1))
    return ""


def _strip_number_prefix(title: str) -> str:
    stripped = _NUMBER_PREFIX_RE.sub("", title).strip()
    return stripped or title.strip()


def number_outline(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters = [0] * 6
    has_document_root = bool(items and int(items[0].get("level", 1)) == 1)
    numbered: list[dict[str, Any]] = []
    for position, item in enumerate(items):
        level = max(1, min(6, int(item["level"])))
        if position == 0 and has_document_root:
            number = ""
        else:
            number_level = max(1, level - 1) if has_document_root else level
            counters[number_level - 1] += 1
            for index in range(number_level, 6):
                counters[index] = 0
            for index in range(number_level - 1):
                if counters[index] == 0:
                    counters[index] = 1
            number = ".".join(str(value) for value in counters[:number_level] if value > 0)
        numbered.append({**item, "number": number, "title": _strip_number_prefix(str(item["title"]))})
    return numbered


def render_markdown(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in items:
        prefix = f"{item['number']} " if item.get("number") else ""
        lines.append(f"{'#' * int(item['level'])} {prefix}{item['title']}")
    return "\n\n".join(lines)
