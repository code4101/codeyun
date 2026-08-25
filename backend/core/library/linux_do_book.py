from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from curl_cffi import requests
from markdownify import markdownify as html_to_markdown

from backend.core.tools.web_outline import SourceHeading, build_rule_outline, number_outline
from backend.core.library.dynamic_book_pagination import dynamic_book_html_page_count


LINUX_DO_HOSTS = {"linux.do", "www.linux.do"}
TOPIC_URL_RE = re.compile(r"^/t/(?:topic|[^/]+)/(?P<topic_id>\d+)(?:/\d+)?/?$")
NOISE_ONLY_RE = re.compile(
    r"^(?:感谢|谢谢|收藏|学习|蹲|mark|cy|插眼|前排|占楼|牛|厉害|好文|支持|"
    r"受教|膜拜|催更|感谢分享|写得真好|看不懂|字都认识|略懂|不明觉厉)"
    r"[了的呀啊呢吧～~！!。,.，\s\d]*$",
    re.IGNORECASE,
)
PRAISE_PREFIX_RE = re.compile(r"^(?:佬|大佬|楼主)?(?:写得)?(?:真)?(?:太)?(?:好|强|厉害|牛)")
MAX_SELECTED_REPLIES = 14


class LinuxDoBookError(RuntimeError):
    pass


@dataclass(slots=True)
class LinuxDoTocItem:
    title: str
    number: str
    level: int
    anchor: str
    parent_anchor: str | None = None
    source_post_number: int | None = None
    inferred: bool = False


@dataclass(slots=True)
class LinuxDoBookDocument:
    topic_id: int
    title: str
    author: str
    source_url: str
    content_html: str
    content_markdown: str
    toc: list[LinuxDoTocItem]
    revision: str
    post_count: int
    selected_reply_count: int
    imported_at: float
    estimated_page_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["toc"] = [asdict(item) for item in self.toc]
        return payload


def normalize_discussion_numbering(document: LinuxDoBookDocument) -> LinuxDoBookDocument:
    """Remove redundant chapter numbers from floor-labelled discussion entries."""
    numbered_discussions = [
        (item.anchor, item.number, item.title)
        for item in document.toc
        if item.source_post_number is not None and item.number
    ]
    if not numbered_discussions:
        return document

    content = BeautifulSoup(document.content_html, "html.parser")
    for anchor, number, title in numbered_discussions:
        item = next(entry for entry in document.toc if entry.anchor == anchor)
        item.number = ""
        heading = content.find(id=anchor)
        if isinstance(heading, Tag) and heading.get_text(" ", strip=True) == f"{number} {title}":
            heading.clear()
            heading.string = title
    document.content_html = str(content)
    return document


def parse_linux_do_topic_url(url: str) -> tuple[int, str]:
    parsed = urlsplit(str(url).strip())
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() not in LINUX_DO_HOSTS:
        raise LinuxDoBookError("目前只支持导入 linux.do 的公开主题地址")
    match = TOPIC_URL_RE.match(parsed.path)
    if match is None:
        raise LinuxDoBookError("请输入形如 https://linux.do/t/topic/2538870 的主题地址")
    topic_id = int(match.group("topic_id"))
    return topic_id, f"https://linux.do/t/topic/{topic_id}"


def _fetch_topic_page(
    client: requests.Session,
    canonical_url: str,
    page: int,
    *,
    max_attempts: int = 7,
) -> dict[str, Any]:
    url = f"{canonical_url}.json"
    last_status = 0
    for attempt in range(max_attempts):
        try:
            response = client.get(
                url,
                params={"page": page},
                headers={"Accept": "application/json"},
                timeout=30,
            )
        except Exception as exc:
            if attempt + 1 >= max_attempts:
                raise LinuxDoBookError(f"读取 LINUX DO 第 {page} 页失败：{exc}") from exc
            time.sleep(min(4.0, 0.6 * (2**attempt)))
            continue
        last_status = response.status_code
        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise LinuxDoBookError(f"LINUX DO 第 {page} 页没有返回有效主题数据") from exc
        if response.status_code not in {403, 429, 500, 502, 503, 504} or attempt + 1 >= max_attempts:
            break
        retry_after = response.headers.get("retry-after", "")
        delay = (
            float(retry_after)
            if retry_after.replace(".", "", 1).isdigit()
            else min(24.0, 1.25 * (2**attempt))
        )
        time.sleep(max(0.4, delay))
    raise LinuxDoBookError(f"LINUX DO 第 {page} 页返回 HTTP {last_status or '错误'}")


def fetch_linux_do_topic(url: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    _topic_id, canonical_url = parse_linux_do_topic_url(url)
    with requests.Session(impersonate="chrome") as client:
        first = _fetch_topic_page(client, canonical_url, 1)
        post_stream = first.get("post_stream") or {}
        first_posts = list(post_stream.get("posts") or [])
        if not first_posts:
            raise LinuxDoBookError("主题中没有可导入的公开内容")
        post_count = max(int(first.get("posts_count") or len(first_posts)), len(first_posts))
        pages = max(1, math.ceil(post_count / max(1, len(first_posts))))
        posts = list(first_posts)
        for page in range(2, pages + 1):
            # Keep one Cloudflare session and avoid bursting the public endpoint.
            time.sleep(0.9)
            payload = _fetch_topic_page(client, canonical_url, page)
            page_posts = list((payload.get("post_stream") or {}).get("posts") or [])
            if not page_posts:
                break
            posts.extend(page_posts)
    unique_posts = {int(post["id"]): post for post in posts if post.get("id") is not None}
    ordered = sorted(unique_posts.values(), key=lambda item: int(item.get("post_number") or 0))
    return first, ordered, canonical_url


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _heading_context(node: Tag) -> str:
    parts: list[str] = []
    for sibling in node.previous_siblings:
        if isinstance(sibling, Tag) and re.fullmatch(r"h[1-6]", sibling.name or ""):
            break
        text = _clean_text(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
        if text:
            parts.append(text)
        if sum(map(len, parts)) >= 260:
            break
    return "前文：" + _clean_text(" ".join(reversed(parts)))[-280:]


def _normalize_discourse_html(raw_html: str, base_url: str) -> BeautifulSoup:
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for node in soup.select("script, style, form, button, .post-menu-area, .topic-map"):
        node.decompose()
    # Discourse appends a metadata row to lightbox images.  It contains two SVG
    # icons whose symbol definitions only exist on the source site; once imported,
    # the empty SVG elements fall back to large intrinsic boxes and leave hundreds
    # of pixels of whitespace around the image.  Keep the original-image link but
    # reduce the site-specific lightbox markup to a stable document structure.
    for wrapper in list(soup.select(".lightbox-wrapper")):
        for metadata in wrapper.select(".meta"):
            metadata.decompose()
        anchor = wrapper.find("a", class_="lightbox")
        image = wrapper.find("img")
        if anchor is not None:
            anchor["class"] = ["imported-book-image-link"]
            anchor.attrs.pop("data-download-href", None)
        if image is None:
            wrapper.unwrap()
            continue
        wrapper["class"] = ["imported-book-image"]
        parent = wrapper.parent
        if isinstance(parent, Tag) and parent.name == "p" and not _clean_text(parent.get_text(" ", strip=True)):
            parent.replace_with(wrapper)
    for image in list(soup.find_all("img")):
        source = str(image.get("src") or "")
        if "/emoji/" in source or "twemoji" in source:
            replacement = soup.new_string(str(image.get("alt") or ""))
            image.replace_with(replacement)
            continue
        if source:
            image["src"] = urljoin(base_url, source)
        image.attrs.pop("srcset", None)
        image.attrs.pop("data-src", None)
        image.attrs.pop("data-small-upload", None)
        image["loading"] = "lazy"
    for media in soup.find_all(["video", "source"]):
        source = str(media.get("src") or "")
        if source:
            media["src"] = urljoin(base_url, source)
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "")
        if href:
            anchor["href"] = urljoin(base_url, href)
            anchor["target"] = "_blank"
            anchor["rel"] = "noopener noreferrer"
    return soup


def _post_like_count(post: dict[str, Any]) -> int:
    return max(
        [int(item.get("count") or 0) for item in post.get("actions_summary") or [] if item.get("id") == 2],
        default=0,
    )


def _reply_signals(post: dict[str, Any]) -> tuple[int, str, BeautifulSoup]:
    soup = _normalize_discourse_html(str(post.get("cooked") or ""), "https://linux.do/")
    scoring_soup = BeautifulSoup(str(soup), "html.parser")
    for quote in scoring_soup.select("aside.quote"):
        quote.decompose()
    text = _clean_text(scoring_soup.get_text(" ", strip=True))
    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    code_count = len(scoring_soup.find_all(["pre", "code"])); link_count = len(scoring_soup.find_all("a"))
    structure_count = len(scoring_soup.find_all(["ul", "ol", "table", "blockquote"])); paragraph_count = len(scoring_soup.find_all("p"))
    likes = _post_like_count(post)
    score = cjk_count + code_count * 120 + link_count * 55 + structure_count * 60
    score += min(likes, 20) * 8 + min(int(post.get("reply_count") or 0), 4) * 20
    if PRAISE_PREFIX_RE.match(text) and cjk_count < 120:
        score -= 140
    for quote in soup.select("aside.quote"):
        quoted_by = _clean_text(str(quote.get("data-display-name") or quote.get("data-username") or "读者"))
        quote_body = quote.find("blockquote")
        quote_text = _clean_text(
            quote_body.get_text(" ", strip=True) if isinstance(quote_body, Tag) else quote.get_text(" ", strip=True)
        )
        compact = soup.new_tag("blockquote")
        compact["class"] = ["reply-context"]
        compact["data-speaker"] = quoted_by
        compact.string = quote_text[:360] + ("…" if len(quote_text) > 360 else "")
        quote.replace_with(compact)
    return score, text, soup


def _append_discussion_content(
    destination: Tag,
    reply_soup: BeautifulSoup,
    *,
    responder: str,
) -> None:
    children = [
        child for child in list(reply_soup.contents)
        if not (isinstance(child, str) and not child.strip())
    ]
    has_questions = any(
        isinstance(child, Tag) and "reply-context" in (child.get("class") or [])
        for child in children
    )
    current_answer: Tag | None = None

    def speaker_label(container: Tag, text: str) -> None:
        label = reply_soup.new_tag("p")
        label["class"] = ["discussion-speaker"]
        strong = reply_soup.new_tag("strong")
        strong.string = text
        label.append(strong)
        container.append(label)

    for child in children:
        is_question = isinstance(child, Tag) and "reply-context" in (child.get("class") or [])
        if is_question:
            question = reply_soup.new_tag("section")
            question["class"] = ["discussion-turn", "is-question"]
            speaker = _clean_text(str(child.get("data-speaker") or "读者"))
            child.attrs.pop("data-speaker", None)
            speaker_label(question, f"{speaker} 提问")
            question.append(child.extract())
            destination.append(question)
            current_answer = None
            continue
        if current_answer is None:
            current_answer = reply_soup.new_tag("section")
            current_answer["class"] = ["discussion-turn", "is-answer"]
            speaker_label(current_answer, f"{responder} {'回复' if has_questions else '发言'}")
            destination.append(current_answer)
        current_answer.append(child.extract())


def select_substantive_replies(
    posts: list[dict[str, Any]],
    *,
    original_poster: str,
    limit: int = MAX_SELECTED_REPLIES,
) -> list[tuple[dict[str, Any], BeautifulSoup]]:
    candidates: list[tuple[int, int, dict[str, Any], BeautifulSoup]] = []
    for post in posts:
        score, text, soup = _reply_signals(post)
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
        has_structured_content = bool(soup.find(["pre", "code", "ul", "ol", "table"]))
        has_link = soup.find("a") is not None
        has_text_link = any(anchor.find("img") is None for anchor in soup.find_all("a"))
        is_original_poster = str(post.get("username") or "") == original_poster
        if not text or NOISE_ONLY_RE.fullmatch(text) or cjk_count < 12 and not has_link:
            continue
        substantive = (
            cjk_count >= 120
            or has_structured_content
            or (has_text_link and cjk_count >= 18)
            or (is_original_poster and (cjk_count >= 42 or has_text_link))
        )
        if not substantive:
            continue
        if is_original_poster:
            score += 180
        candidates.append((score, int(post.get("post_number") or 0), post, soup))
    chosen = sorted(candidates, key=lambda item: (-item[0], item[1]))[: max(0, limit)]
    return [(post, soup) for _score, _floor, post, soup in sorted(chosen, key=lambda item: item[1])]


def _anchor_for(number: str, fallback: str) -> str:
    if number:
        return "section-" + number.replace(".", "-")
    digest = hashlib.sha1(fallback.encode("utf-8")).hexdigest()[:10]
    return f"section-{digest}"


def compose_linux_do_book(topic: dict[str, Any], posts: list[dict[str, Any]], canonical_url: str) -> LinuxDoBookDocument:
    if not posts:
        raise LinuxDoBookError("主题正文为空")
    first_post = posts[0]
    title = _clean_text(str(topic.get("title") or first_post.get("topic_title") or "LINUX DO 主题"))
    author = _clean_text(str(first_post.get("username") or first_post.get("name") or ""))
    original_poster = str(first_post.get("username") or "")
    body = _normalize_discourse_html(str(first_post.get("cooked") or ""), canonical_url)
    heading_nodes = [node for node in body.find_all(re.compile(r"^h[1-6]$")) if isinstance(node, Tag)]
    source_headings = [
        SourceHeading(
            source_index=index,
            title=_clean_text(node.get_text(" ", strip=True)),
            html_level=int(node.name[1]),
            context=_heading_context(node),
        )
        for index, node in enumerate(heading_nodes)
        if _clean_text(node.get_text(" ", strip=True))
    ]
    outline = build_rule_outline(title, source_headings)
    selected_replies = select_substantive_replies(posts[1:], original_poster=original_poster)
    if selected_replies:
        outline.append({"title": "精选讨论", "level": 2, "source_index": None, "inferred": True, "discussion": True})
        for post, _soup in selected_replies:
            floor = int(post.get("post_number") or 0)
            outline.append({
                "title": f"第 {floor} 楼",
                "level": 3,
                "source_index": None,
                "inferred": False,
                "discussion_post_number": floor,
            })
    numbered = number_outline(outline)
    toc: list[LinuxDoTocItem] = []
    real_by_index: dict[int, dict[str, Any]] = {}
    pending_inferred: list[dict[str, Any]] = []
    discussion_items: list[dict[str, Any]] = []
    for item in numbered[1:]:
        item["anchor"] = _anchor_for(str(item.get("number") or ""), str(item.get("title") or ""))
        toc.append(LinuxDoTocItem(
            title=str(item["title"]),
            number="" if item.get("discussion_post_number") is not None else str(item["number"]),
            level=int(item["level"]),
            anchor=str(item["anchor"]),
            source_post_number=item.get("discussion_post_number"), inferred=bool(item.get("inferred")),
        ))
        if item.get("discussion") or item.get("discussion_post_number"):
            discussion_items.append(item)
        elif item.get("source_index") is None:
            pending_inferred.append(item)
        else:
            item["pending_inferred"] = pending_inferred
            pending_inferred = []
            real_by_index[int(item["source_index"])] = item

    for index, node in enumerate(heading_nodes):
        item = real_by_index.get(index)
        if item is None:
            continue
        for inferred in item.get("pending_inferred") or []:
            inserted = body.new_tag(f"h{min(6, int(inferred['level']))}")
            inserted["id"] = inferred["anchor"]
            inserted["data-inferred-heading"] = "true"
            inserted.string = f"{inferred['number']} {inferred['title']}"
            node.insert_before(inserted)
        node.name = f"h{min(6, int(item['level']))}"
        node["id"] = item["anchor"]
        node.clear()
        node.string = f"{item['number']} {item['title']}"

    root = body.new_tag("header")
    root["class"] = ["imported-book-title"]
    title_node = body.new_tag("h1"); title_node.string = title; root.append(title_node)
    byline = body.new_tag("p"); byline["class"] = ["imported-book-byline"]
    byline.string = f"作者：{author} · 来源：LINUX DO"; root.append(byline)
    body.insert(0, root)

    if selected_replies:
        discussion_root_item = next(item for item in discussion_items if item.get("discussion"))
        discussion = body.new_tag("section"); discussion["class"] = ["selected-discussion"]
        heading = body.new_tag("h2"); heading["id"] = discussion_root_item["anchor"]
        heading.string = f"{discussion_root_item['number']} 精选讨论"; discussion.append(heading)
        comment_items = [item for item in discussion_items if item.get("discussion_post_number")]
        item_by_floor = {int(item["discussion_post_number"]): item for item in comment_items}
        for post, reply_soup in selected_replies:
            floor = int(post.get("post_number") or 0); item = item_by_floor[floor]
            responder = _clean_text(str(post.get("display_username") or post.get("username") or "读者"))
            article = body.new_tag("article"); article["class"] = ["selected-reply"]
            reply_heading = body.new_tag("h3"); reply_heading["id"] = item["anchor"]
            reply_heading.string = str(item["title"]); article.append(reply_heading)
            _append_discussion_content(article, reply_soup, responder=responder)
            discussion.append(article)
        body.append(discussion)

    source_footer = body.new_tag("p"); source_footer["class"] = ["imported-book-source"]
    source_link = body.new_tag("a", href=canonical_url, target="_blank", rel="noopener noreferrer")
    source_link.string = "查看 LINUX DO 原主题"; source_footer.append(source_link); body.append(source_footer)
    content_html = str(body)
    markdown_body = BeautifulSoup(content_html, "html.parser")
    for math_node in markdown_body.select(".math"):
        expression = math_node.get_text(" ", strip=True)
        if math_node.name == "div":
            math_node.replace_with(markdown_body.new_string(f"\n$$\n{expression}\n$$\n"))
        else:
            math_node.replace_with(markdown_body.new_string(f"${expression}$"))
    content_markdown = html_to_markdown(
        str(markdown_body),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    ).strip() + "\n"
    revision_source = f"{first_post.get('updated_at')}|{len(posts)}|{content_html}"
    revision = hashlib.sha256(revision_source.encode("utf-8")).hexdigest()
    topic_id, _canonical = parse_linux_do_topic_url(canonical_url)
    return LinuxDoBookDocument(
        topic_id=topic_id, title=title, author=author, source_url=canonical_url,
        content_html=content_html, content_markdown=content_markdown, toc=toc, revision=revision,
        post_count=len(posts), selected_reply_count=len(selected_replies), imported_at=time.time(),
        estimated_page_count=dynamic_book_html_page_count(content_html),
    )


def import_linux_do_book(url: str) -> LinuxDoBookDocument:
    topic, posts, canonical_url = fetch_linux_do_topic(url)
    return compose_linux_do_book(topic, posts, canonical_url)
