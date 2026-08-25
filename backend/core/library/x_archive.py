from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Iterable
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from sqlmodel import Session, select

from backend.core.ai.chat import chat_with_provider
from backend.core.jobs.local_runtime import submit_local_job_once
from backend.core.library.dynamic_book_pagination import dynamic_book_html_page_count
from backend.core.library.linux_do_book import LinuxDoBookDocument, LinuxDoTocItem
from backend.core.settings import get_settings
from backend.models import (
    AppSetting,
    LibraryBookAsset,
    LibraryBookPlacement,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
)


TIBO_X_ARCHIVE_TASK_KEY = "tibo_x_archive"
TIBO_X_ARCHIVE_CONFIG_KEY = f"background_task.{TIBO_X_ARCHIVE_TASK_KEY}.config"
TIBO_X_ARCHIVE_HANDLE = "thsottiaux"
TIBO_X_ARCHIVE_TITLE = "Tibo X 消息摘录"
TIBO_X_ARCHIVE_AUTHOR = "Tibo"
TIBO_X_ARCHIVE_LEGACY_AUTHORS = {"Tibo（Thibault Sottiaux）"}
TIBO_X_ARCHIVE_LOOKBACK_DAYS = 30
X_ARCHIVE_RENDER_VERSION = 6
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")
NITTER_RSS_URL_TEMPLATE = "https://nitter.net/{handle}/rss"
X_PROFILE_URL_TEMPLATE = "https://x.com/{handle}"
RSS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36"
)


@dataclass(frozen=True)
class XPost:
    id: str
    handle: str
    url: str
    created_at: str
    created_ts: float
    text: str
    quoted_author: str = ""
    quoted_text: str = ""
    images: list[str] = field(default_factory=list)
    text_zh: str = ""
    quoted_text_zh: str = ""
    captured_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    @property
    def source_hash(self) -> str:
        payload = "\0".join((self.text, self.quoted_author, self.quoted_text))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class XArchiveResult:
    status: str
    book_id: str | None = None
    fetched_count: int = 0
    new_count: int = 0
    translated_count: int = 0
    total_count: int = 0
    message: str = ""


class XArchiveStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS x_posts (
                    id TEXT PRIMARY KEY,
                    handle TEXT NOT NULL,
                    url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    text TEXT NOT NULL,
                    quoted_author TEXT NOT NULL DEFAULT '',
                    quoted_text TEXT NOT NULL DEFAULT '',
                    images_json TEXT NOT NULL DEFAULT '[]',
                    text_zh TEXT NOT NULL DEFAULT '',
                    quoted_text_zh TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_x_posts_handle_created "
                "ON x_posts(handle, created_ts DESC)"
            )

    def existing_ids(self, handle: str) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM x_posts WHERE handle = ?",
                (handle,),
            ).fetchall()
        return {str(row["id"]) for row in rows}

    def upsert_many(self, posts: Iterable[XPost]) -> int:
        rows = list(posts)
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO x_posts (
                    id, handle, url, created_at, created_ts, text, quoted_author,
                    quoted_text, images_json, text_zh, quoted_text_zh, source_hash,
                    captured_at
                ) VALUES (
                    :id, :handle, :url, :created_at, :created_ts, :text, :quoted_author,
                    :quoted_text, :images_json, :text_zh, :quoted_text_zh, :source_hash,
                    :captured_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    handle = excluded.handle,
                    url = excluded.url,
                    created_at = excluded.created_at,
                    created_ts = excluded.created_ts,
                    text = excluded.text,
                    quoted_author = excluded.quoted_author,
                    quoted_text = excluded.quoted_text,
                    images_json = CASE
                        WHEN excluded.images_json != '[]' THEN excluded.images_json
                        ELSE x_posts.images_json
                    END,
                    text_zh = CASE
                        WHEN excluded.source_hash = x_posts.source_hash THEN x_posts.text_zh
                        ELSE ''
                    END,
                    quoted_text_zh = CASE
                        WHEN excluded.source_hash = x_posts.source_hash THEN x_posts.quoted_text_zh
                        ELSE ''
                    END,
                    source_hash = excluded.source_hash,
                    captured_at = excluded.captured_at
                """,
                [self._database_row(post) for post in rows],
            )
        return len(rows)

    @staticmethod
    def _database_row(post: XPost) -> dict[str, Any]:
        row = asdict(post)
        row["images_json"] = json.dumps(post.images, ensure_ascii=False)
        row["source_hash"] = post.source_hash
        row.pop("images")
        return row

    def untranslated(self, handle: str) -> list[XPost]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM x_posts
                WHERE handle = ? AND text != '' AND text_zh = ''
                ORDER BY created_ts DESC, id DESC
                """,
                (handle,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def save_translations(self, translations: dict[str, tuple[str, str]]) -> int:
        rows = [
            (text_zh.strip(), quoted_text_zh.strip(), post_id)
            for post_id, (text_zh, quoted_text_zh) in translations.items()
            if text_zh.strip()
        ]
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                "UPDATE x_posts SET text_zh = ?, quoted_text_zh = ? WHERE id = ?",
                rows,
            )
        return len(rows)

    def list_posts(self, handle: str, *, translated_only: bool = False) -> list[XPost]:
        translated_clause = " AND text_zh != ''" if translated_only else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM x_posts
                WHERE handle = ?{translated_clause}
                ORDER BY created_ts DESC, id DESC
                """,
                (handle,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> XPost:
        return XPost(
            id=str(row["id"]),
            handle=str(row["handle"]),
            url=str(row["url"]),
            created_at=str(row["created_at"]),
            created_ts=float(row["created_ts"]),
            text=str(row["text"]),
            quoted_author=str(row["quoted_author"]),
            quoted_text=str(row["quoted_text"]),
            images=json.loads(row["images_json"] or "[]"),
            text_zh=str(row["text_zh"]),
            quoted_text_zh=str(row["quoted_text_zh"]),
            captured_at=str(row["captured_at"]),
        )


def _normalize_x_url(value: str, *, handle: str, post_id: str) -> str:
    match = re.search(r"/status/(\d+)", str(value or ""))
    resolved_id = match.group(1) if match else post_id
    return f"https://x.com/{handle}/status/{resolved_id}"


def _normalize_media_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.hostname not in {"nitter.net", "www.nitter.net"} or not parsed.path.startswith("/pic/"):
        return url
    original_path = unquote(parsed.path[len("/pic/") :]).lstrip("/")
    if original_path.startswith(("media/", "amplify_video_thumb/", "ext_tw_video_thumb/")):
        return f"https://pbs.twimg.com/{original_path}"
    if original_path.startswith(("http://", "https://")):
        return original_path
    return url


def _direct_paragraph_text(soup: BeautifulSoup) -> str:
    parts: list[str] = []
    for child in soup.contents:
        if isinstance(child, Tag) and child.name == "hr":
            break
        if isinstance(child, Tag) and child.name == "p":
            text = child.get_text("\n", strip=True)
            if text:
                parts.append(text)
    return "\n\n".join(parts).strip()


def normalize_quoted_text(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\n{2,}", str(value or "").strip()) if part.strip()]
    unique: list[str] = []
    for part in parts:
        if any(existing == part or existing.startswith(f"{part}\n") for existing in unique):
            continue
        replaced = False
        for index, existing in enumerate(unique):
            if part.startswith(f"{existing}\n"):
                unique[index] = part
                replaced = True
                break
        if not replaced:
            unique.append(part)
    return "\n\n".join(unique)


def _quoted_payload(soup: BeautifulSoup) -> tuple[str, str]:
    blockquote = soup.find("blockquote")
    if not isinstance(blockquote, Tag):
        return "", ""
    author_tag = blockquote.find("b")
    author = author_tag.get_text(" ", strip=True) if isinstance(author_tag, Tag) else ""
    parts: list[str] = []
    for paragraph in blockquote.find_all("p", recursive=False):
        text = paragraph.get_text("\n", strip=True)
        if text and text not in parts:
            parts.append(text)
    return author, normalize_quoted_text("\n\n".join(parts))


def parse_nitter_rss(data: bytes, *, handle: str) -> list[XPost]:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data)
    posts: list[XPost] = []
    for item in root.findall("./channel/item"):
        post_id = str(item.findtext("guid") or "").strip()
        published = str(item.findtext("pubDate") or "").strip()
        if not post_id or not published:
            continue
        created = parsedate_to_datetime(published)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        created = created.astimezone(DISPLAY_TIMEZONE)
        description = str(item.findtext("description") or "")
        soup = BeautifulSoup(description, "html.parser")
        text = _direct_paragraph_text(soup)
        if not text:
            text = str(item.findtext("title") or "").strip()
        quoted_author, quoted_text = _quoted_payload(soup)
        images: list[str] = []
        for image in soup.find_all("img"):
            normalized = _normalize_media_url(str(image.get("src") or ""))
            if normalized and normalized not in images:
                images.append(normalized)
        posts.append(
            XPost(
                id=post_id,
                handle=handle,
                url=_normalize_x_url(str(item.findtext("link") or ""), handle=handle, post_id=post_id),
                created_at=created.strftime("%Y-%m-%d %H:%M"),
                created_ts=created.timestamp(),
                text=text,
                quoted_author=quoted_author,
                quoted_text=quoted_text,
                images=images,
            )
        )
    return posts


def fetch_nitter_rss_page(
    *,
    handle: str,
    cursor: str = "",
    timeout_seconds: float = 30,
) -> tuple[bytes, str]:
    url = NITTER_RSS_URL_TEMPLATE.format(handle=quote(handle))
    if cursor:
        url = f"{url}?cursor={quote(cursor)}"
    request = Request(
        url,
        headers={
            "User-Agent": RSS_USER_AGENT,
            "Accept": "application/rss+xml",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return response.read(), str(response.headers.get("Min-Id") or "").strip()


def crawl_x_profile(
    *,
    handle: str,
    since: datetime,
    max_pages: int = 12,
    fetch_page: Callable[..., tuple[bytes, str]] = fetch_nitter_rss_page,
) -> list[XPost]:
    threshold = since.astimezone(DISPLAY_TIMEZONE).timestamp()
    posts: dict[str, XPost] = {}
    cursor = ""
    seen_cursors: set[str] = set()
    for _ in range(max(1, max_pages)):
        data, next_cursor = fetch_page(handle=handle, cursor=cursor)
        page_posts = parse_nitter_rss(data, handle=handle)
        if not page_posts:
            break
        for post in page_posts:
            if post.created_ts >= threshold:
                posts[post.id] = post
        if min(post.created_ts for post in page_posts) < threshold:
            break
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return sorted(posts.values(), key=lambda post: (post.created_ts, post.id), reverse=True)


TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text_zh": {"type": "string"},
                    "quoted_text_zh": {"type": "string"},
                },
                "required": ["id", "text_zh", "quoted_text_zh"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def translate_x_posts(
    posts: Iterable[XPost],
    *,
    batch_size: int = 6,
    provider_id: str = "ollama",
    model: str = "qwen3.5:4b-instruct",
    chat: Callable[..., dict[str, Any]] = chat_with_provider,
) -> dict[str, tuple[str, str]]:
    rows = list(posts)
    translations: dict[str, tuple[str, str]] = {}
    for start in range(0, len(rows), max(1, batch_size)):
        batch = rows[start : start + max(1, batch_size)]
        payload = [
            {
                "id": post.id,
                "text": post.text,
                "quoted_author": post.quoted_author,
                "quoted_text": post.quoted_text,
            }
            for post in batch
        ]
        response = chat(
            provider_id=provider_id,
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            ],
            system_prompt=(
                "你是严谨的英译中编辑。把输入数组中每条 X 消息翻译成自然、准确、简洁的中文。"
                "保留人名、账号、URL、产品名、模型名、数字和 emoji；不要总结、解释或添加事实。"
                "quoted_text 为空时 quoted_text_zh 也必须为空。严格按指定 JSON 结构返回。"
            ),
            temperature=0,
            response_format=TRANSLATION_SCHEMA,
            timeout_seconds=180,
        )
        try:
            result = json.loads(str(response.get("content") or ""))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("X 消息翻译结果不是有效 JSON。") from exc
        by_id = {post.id: post for post in batch}
        for item in result.get("translations") or []:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("id") or "")
            text_zh = str(item.get("text_zh") or "").strip()
            quoted_text_zh = str(item.get("quoted_text_zh") or "").strip()
            if post_id in by_id and text_zh:
                translations[post_id] = (text_zh, quoted_text_zh)
        missing = [post.id for post in batch if post.id not in translations]
        if missing:
            if len(batch) == 1:
                raise RuntimeError("X 消息翻译结果缺少当前记录。")
            missing_ids = set(missing)
            translations.update(
                translate_x_posts(
                    [post for post in batch if post.id in missing_ids],
                    batch_size=max(1, len(missing) // 2),
                    provider_id=provider_id,
                    model=model,
                    chat=chat,
                )
            )
    return translations


def _google_translate_text(text: str, *, timeout_seconds: float = 30) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    url = "https://translate.googleapis.com/translate_a/single?" + urlencode(
        {
            "client": "gtx",
            "sl": "auto",
            "tl": "zh-CN",
            "dt": "t",
            "q": source,
        }
    )
    request = Request(
        url,
        headers={
            "User-Agent": RSS_USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read())
    segments = payload[0] if isinstance(payload, list) and payload else []
    translated = "".join(
        str(segment[0])
        for segment in segments
        if isinstance(segment, list) and segment and segment[0]
    ).strip()
    if not translated:
        raise RuntimeError("在线翻译返回空文本。")
    return translated


def translate_x_posts_online(
    posts: Iterable[XPost],
    *,
    max_workers: int = 6,
    translate_text: Callable[[str], str] = _google_translate_text,
) -> dict[str, tuple[str, str]]:
    rows = list(posts)
    translations: dict[str, tuple[str, str]] = {}

    def translate_one(post: XPost) -> tuple[str, str, str]:
        return (
            post.id,
            translate_text(post.text),
            translate_text(post.quoted_text) if post.quoted_text else "",
        )

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {executor.submit(translate_one, post): post.id for post in rows}
        for future in as_completed(futures):
            try:
                post_id, text_zh, quoted_text_zh = future.result()
            except Exception:
                continue
            if text_zh:
                translations[post_id] = (text_zh, quoted_text_zh)
    return translations


def _render_x_entry(post: XPost) -> str:
    main_text_zh = html.escape(post.text_zh or post.text).replace("\n", "<br/>")
    main_text_original = html.escape(post.text).replace("\n", "<br/>")
    quote_original_html = ""
    quote_translation_html = ""
    if post.quoted_text:
        quote_text_original = html.escape(post.quoted_text).replace("\n", "<br/>")
        quote_author = html.escape(post.quoted_author)
        quote_original_html = (
            '<div class="x-original-quote" style="margin-top:12px;padding-left:14px;'
            'border-left:3px solid #d8dee7;color:#4b5563;">'
            f'<div style="font-size:12px;color:#89919d;">引用 · {quote_author}</div>'
            f'<div lang="en" style="margin-top:4px;font-size:14px;line-height:22px;">'
            f"{quote_text_original}</div></div>"
        )
        if post.quoted_text_zh:
            quote_text_zh = html.escape(post.quoted_text_zh).replace("\n", "<br/>")
            quote_translation_html = (
                '<div class="x-translated-quote" style="margin-top:10px;padding-left:14px;'
                'border-left:3px solid #d8dee7;color:#4b5563;">'
                f'<div style="font-size:12px;color:#89919d;">引用 · {quote_author}</div>'
                f'<div lang="zh-CN" style="margin-top:4px;font-size:14px;line-height:22px;">'
                f"{quote_text_zh}</div></div>"
            )
    images_html = ""
    if post.images:
        columns = min(len(post.images), 3)
        image_items = "".join(
            '<a href="{url}" style="display:block;overflow:hidden;'
            'border-radius:8px;line-height:0;"><img alt="X 配图" loading="lazy" '
            'src="{url}" style="display:block;width:100%;height:auto;"/></a>'.format(
                url=html.escape(url, quote=True)
            )
            for url in post.images
        )
        images_html = (
            f'<div style="display:grid;grid-template-columns:repeat({columns},1fr);'
            f'gap:4px;width:100%;max-width:542px;margin-top:10px;">{image_items}</div>'
        )
    translation_html = ""
    if post.text_zh:
        translation_html = (
            '<div class="x-translation" style="margin-top:12px;padding-top:10px;'
            'border-top:1px dashed #e5e7eb;color:#6b7280;">'
            '<div style="margin-bottom:4px;font-size:12px;line-height:18px;color:#9ca3af;">中文翻译</div>'
            f'<div lang="zh-CN" style="font-size:14px;line-height:22px;">{main_text_zh}</div>'
            f"{quote_translation_html}</div>"
        )
    return (
        f'<figure class="x-entry" id="x-{html.escape(post.id, quote=True)}" '
        'data-book-page-atomic="true" '
        'style="box-sizing:border-box;width:100%;max-width:640px;margin:0 auto;'
        'padding:18px 0 22px;border-bottom:1px solid #e5e7eb;color:#333;">'
        '<div style="display:flex;justify-content:space-between;gap:12px;'
        'font-size:13px;line-height:18px;color:#939393;">'
        f'<span>{html.escape(post.created_at)}</span>'
        f'<a href="{html.escape(post.url, quote=True)}" '
        'style="color:#777;text-decoration:none;">查看原帖</a></div>'
        f'<div lang="en" style="margin-top:5px;font-size:15px;line-height:24px;">'
        f"{main_text_original}</div>"
        f"{quote_original_html}{images_html}{translation_html}</figure>"
    )


def build_x_book_document(
    posts: Iterable[XPost],
    *,
    topic_id: int,
    title: str,
    author: str,
    source_url: str,
    imported_at: float,
) -> LinuxDoBookDocument:
    ordered = sorted(posts, key=lambda post: (post.created_ts, post.id), reverse=True)
    months: dict[str, list[XPost]] = {}
    for post in ordered:
        months.setdefault(post.created_at[:7], []).append(post)
    articles: list[str] = []
    toc: list[LinuxDoTocItem] = []
    for month, month_posts in months.items():
        anchor = f"month-{month}"
        heading = f"{month}（{len(month_posts)}则）"
        articles.append(
            f'<article data-article-id="{anchor}" '
            'style="box-sizing:border-box;width:100%;max-width:680px;margin:0 auto 34px;">'
            f'<h1 id="{anchor}" style="max-width:640px;margin:0 auto 18px;'
            f'font-size:24px;line-height:1.35;color:#1f2937;">{heading}</h1>'
            + "".join(_render_x_entry(post) for post in month_posts)
            + "</article>"
        )
        toc.append(
            LinuxDoTocItem(
                title=heading,
                number="",
                level=2,
                anchor=anchor,
                source_post_number=None,
            )
        )
    content_html = "".join(articles)
    return LinuxDoBookDocument(
        topic_id=topic_id,
        title=title,
        author=author,
        source_url=source_url,
        content_html=content_html,
        content_markdown="",
        toc=toc,
        revision=hashlib.sha256(content_html.encode("utf-8")).hexdigest(),
        post_count=len(ordered),
        selected_reply_count=0,
        imported_at=imported_at,
        estimated_page_count=dynamic_book_html_page_count(content_html),
    )


def _asset_id(owner_user_id: int, handle: str) -> str:
    return f"x-archive:{int(owner_user_id)}:{handle.lower()}"


def _topic_id(owner_user_id: int, handle: str) -> int:
    digest = hashlib.sha256(_asset_id(owner_user_id, handle).encode("utf-8")).hexdigest()
    return -int(digest[:14], 16)


def _archive_root(handle: str) -> Path:
    return get_settings().data_dir / "library" / "x" / handle.lower()


def _book_path(owner_user_id: int, topic_id: int) -> Path:
    return (
        get_settings().data_dir
        / "library-books"
        / f"user_{int(owner_user_id)}"
        / "linux-do"
        / str(int(topic_id))
        / "book.json"
    )


def _write_document(owner_user_id: int, document: LinuxDoBookDocument) -> None:
    path = _book_path(owner_user_id, document.topic_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _ensure_placement(
    session: Session,
    asset: LibraryBookAsset,
    user_id: int,
    now: float,
) -> None:
    existing = session.exec(
        select(LibraryBookPlacement)
        .where(LibraryBookPlacement.book_asset_id == asset.id)
        .where(LibraryBookPlacement.user_id == int(user_id))
    ).first()
    if existing is not None:
        return

    reference = session.exec(
        select(LibraryBookPlacement)
        .join(LibraryBookAsset, LibraryBookAsset.id == LibraryBookPlacement.book_asset_id)
        .where(LibraryBookPlacement.user_id == int(user_id))
        .where(LibraryBookAsset.source_kind.startswith("weibo-archive:"))
        .order_by(LibraryBookPlacement.updated_at.desc())
    ).first()
    if reference is not None:
        shelf = session.get(PdfLibraryBookshelf, reference.bookshelf_id)
        shelf_index = int(reference.shelf_index)
    else:
        shelf = session.exec(
            select(PdfLibraryBookshelf)
            .where(PdfLibraryBookshelf.user_id == int(user_id))
            .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
        ).first()
        shelf_index = 0
    if shelf is None:
        shelf = PdfLibraryBookshelf(
            user_id=int(user_id),
            name="1",
            sort_index=0,
            created_at=now,
            updated_at=now,
        )
        session.add(shelf)
        session.flush()

    positions = [
        *session.exec(
            select(PdfBookshelfPlacement.position_index)
            .where(PdfBookshelfPlacement.user_id == int(user_id))
            .where(PdfBookshelfPlacement.bookshelf_id == shelf.id)
            .where(PdfBookshelfPlacement.shelf_index == shelf_index)
        ).all(),
        *session.exec(
            select(LibraryBookPlacement.position_index)
            .where(LibraryBookPlacement.user_id == int(user_id))
            .where(LibraryBookPlacement.bookshelf_id == shelf.id)
            .where(LibraryBookPlacement.shelf_index == shelf_index)
        ).all(),
    ]
    session.add(
        LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=int(user_id),
            bookshelf_id=shelf.id,
            shelf_index=shelf_index,
            position_index=max([int(value) for value in positions], default=-1) + 1,
            orientation="spine_vertical",
            created_at=now,
            updated_at=now,
        )
    )


def sync_x_archive(
    session: Session,
    *,
    owner_user_id: int,
    handle: str = TIBO_X_ARCHIVE_HANDLE,
    title: str = TIBO_X_ARCHIVE_TITLE,
    author: str = TIBO_X_ARCHIVE_AUTHOR,
    lookback_days: int = TIBO_X_ARCHIVE_LOOKBACK_DAYS,
    now: datetime | None = None,
    crawler: Callable[..., list[XPost]] = crawl_x_profile,
    translator: Callable[..., dict[str, tuple[str, str]]] = translate_x_posts_online,
) -> XArchiveResult:
    handle = handle.strip().lstrip("@").lower()
    current = (now or datetime.now(DISPLAY_TIMEZONE)).astimezone(DISPLAY_TIMEZONE)
    store = XArchiveStore(_archive_root(handle) / "x.sqlite3")
    existing_ids = store.existing_ids(handle)
    fetched = crawler(
        handle=handle,
        since=current - timedelta(days=max(1, int(lookback_days))),
    )
    new_count = len({post.id for post in fetched} - existing_ids)
    store.upsert_many(fetched)
    pending = store.untranslated(handle)
    translations = translator(pending) if pending else {}
    translated_count = store.save_translations(translations)
    posts = store.list_posts(handle, translated_only=True)
    if not posts:
        return XArchiveResult(
            status="translation_pending",
            fetched_count=len(fetched),
            new_count=new_count,
            translated_count=translated_count,
            message="尚无完成中文翻译的 X 消息。",
        )

    asset_id = _asset_id(owner_user_id, handle)
    topic_id = _topic_id(owner_user_id, handle)
    asset = session.get(LibraryBookAsset, asset_id)
    render_version = int((asset.metadata_json or {}).get("render_version") or 0) if asset else 0
    if (
        asset is not None
        and new_count == 0
        and translated_count == 0
        and render_version == X_ARCHIVE_RENDER_VERSION
    ):
        return XArchiveResult(
            status="up_to_date",
            book_id=asset.id,
            fetched_count=len(fetched),
            new_count=0,
            translated_count=0,
            total_count=len(posts),
        )
    imported_at = float((asset.metadata_json or {}).get("imported_at") or time.time()) if asset else time.time()
    document = build_x_book_document(
        posts,
        topic_id=topic_id,
        title=title,
        author=author,
        source_url=X_PROFILE_URL_TEMPLATE.format(handle=handle),
        imported_at=imported_at,
    )
    created = asset is None
    timestamp = time.time()
    if asset is None:
        asset = LibraryBookAsset(
            id=asset_id,
            resource_type="linux-do-book",
            owner_user_id=int(owner_user_id),
            source_kind=f"x-archive:{handle}",
            created_at=timestamp,
        )
    asset.title = title
    asset.author = author
    asset.cover_color = "#111827"
    asset.updated_at = timestamp
    asset.metadata_json = {
        **dict(asset.metadata_json or {}),
        "storage_version": 1,
        "book_kind": "x-archive",
        "dynamic": True,
        "format": "html",
        "topic_id": topic_id,
        "handle": handle,
        "source_url": document.source_url,
        "translation_language": "zh-CN",
        "entry_order": "descending",
        "render_version": X_ARCHIVE_RENDER_VERSION,
        "initial_lookback_days": int(lookback_days),
        "revision": document.revision,
        "toc_count": len(document.toc),
        "post_count": document.post_count,
        "estimated_page_count": document.estimated_page_count,
        "newest_post_at": posts[0].created_at,
        "oldest_post_at": posts[-1].created_at,
        "imported_at": imported_at,
        "updated_at": timestamp,
    }
    session.add(asset)
    _ensure_placement(session, asset, int(owner_user_id), timestamp)
    _write_document(int(owner_user_id), document)
    session.commit()
    return XArchiveResult(
        status="created" if created else "updated",
        book_id=asset.id,
        fetched_count=len(fetched),
        new_count=new_count,
        translated_count=translated_count,
        total_count=len(posts),
    )


def _read_job_config(session: Session) -> dict[str, Any]:
    row = session.get(AppSetting, TIBO_X_ARCHIVE_CONFIG_KEY)
    return dict(row.value or {}) if row is not None else {}


def normalize_tibo_x_archive_author(value: object) -> str:
    author = str(value or "").strip()
    if not author or author in TIBO_X_ARCHIVE_LEGACY_AUTHORS:
        return TIBO_X_ARCHIVE_AUTHOR
    return author


def run_tibo_x_archive_job() -> XArchiveResult:
    from backend.db import engine

    with Session(engine) as session:
        config = _read_job_config(session)
        owner_user_id = int(config.get("owner_user_id") or 0)
        if owner_user_id <= 0:
            return XArchiveResult(
                status="not_configured",
                message="尚未配置 Tibo X 摘录的图书馆所有者。",
            )
        result = sync_x_archive(
            session,
            owner_user_id=owner_user_id,
            handle=str(config.get("handle") or TIBO_X_ARCHIVE_HANDLE),
            title=str(config.get("title") or TIBO_X_ARCHIVE_TITLE),
            author=normalize_tibo_x_archive_author(config.get("author")),
            lookback_days=int(config.get("lookback_days") or TIBO_X_ARCHIVE_LOOKBACK_DAYS),
        )
    print(
        "Tibo X archive job finished: "
        f"status={result.status} fetched={result.fetched_count} new={result.new_count} "
        f"translated={result.translated_count} total={result.total_count}"
    )
    return result


def enqueue_tibo_x_archive_job() -> str | None:
    run, _created = submit_local_job_once(job_type="library.tibo-x-archive", payload={})
    return run.id
