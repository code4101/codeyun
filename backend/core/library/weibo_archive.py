from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from backend.core.library.dynamic_book_pagination import dynamic_book_page_count
from curl_cffi import requests


WEIBO_PROFILE_URL_TEMPLATE = "https://weibo.com/u/{uid}"
LOGIN_OR_RISK_MARKERS = ("登录", "验证码", "安全验证", "访问异常", "扫码")
SINA_BLOG_PLACEHOLDER_URL = "https://s6.sinaimg.cn/thumb180/6c0c0bb3tdf7bdeae9c65"
SINA_BLOG_PLACEHOLDER_ASSET_STEM = hashlib.sha256(
    SINA_BLOG_PLACEHOLDER_URL.encode("utf-8")
).hexdigest()


WEIBO_ARTICLE_EXTRACTOR_JS = r"""
return (() => {
  const uid = arguments[0];
  const permalinkPattern = new RegExp('^/' + uid + '/[A-Za-z0-9]+/?$');
  const cleanText = (value) => String(value || '')
    .replace(/\s*\.\.\.展开\s*$/, '')
    .trim();

  return Array.from(document.querySelectorAll('article')).map((article) => {
    const links = Array.from(article.querySelectorAll('a[href]'));
    const permalink = links.find((link) => {
      try {
        return permalinkPattern.test(new URL(link.href).pathname);
      } catch (_error) {
        return false;
      }
    });
    if (!permalink) return null;

    const url = new URL(permalink.href);
    const header = article.querySelector('header');
    const original = article.querySelector('.wbpro-feed-ogText');
    const reposted = article.querySelector('.wbpro-feed-reText');
    const headerText = header?.innerText || '';
    const metricRows = Array.from(article.querySelectorAll('footer[aria-label]'))
      .map((footer) => String(footer.getAttribute('aria-label') || '').split(','));
    const hasRepost = Boolean(reposted) || /转发微博/.test(headerText);
    const metrics = metricRows[metricRows.length - 1] || [];
    const repostMetrics = hasRepost && metricRows.length > 1 ? metricRows[0] : [];
    const mediaRoots = article.querySelectorAll('.wbpro-feed-content img, .retweet img');
    const images = Array.from(mediaRoots)
      .filter((image) => {
        const className = String(image.className || '').toLowerCase();
        return !className.includes('avatar')
          && !image.closest('.woo-avatar-main, [usercard], [class*="avatar"]');
      })
      .map((image) => image.currentSrc || image.src || '')
      .filter((src) => src
        && !src.includes('timeline_card_small_')
        && !src.includes('/6c0c0bb3tdf7bdeae9c65')
        && !/^https?:\/\/tva[x\d]*\d*\.sinaimg\.cn\/crop\./i.test(src));
    const authorLink = Array.from(header?.querySelectorAll('a[href]') || []).find((link) => {
      try {
        return new URL(link.href).pathname === '/u/' + uid;
      } catch (_error) {
        return false;
      }
    });
    const authorAvatar = header?.querySelector('img.woo-avatar-img');
    const video = article.querySelector('video');
    const videoRoot = video?.closest('.video-js') || article;
    const videoPoster = videoRoot?.querySelector('.vjs-poster img');
    const videoLink = links.find((link) => {
      try {
        return new URL(link.href).hostname === 'video.weibo.com';
      } catch (_error) {
        return false;
      }
    });
    const videoViews = Array.from(article.querySelectorAll('div, span'))
      .map((element) => cleanText(element.innerText))
      .find((text) => /^\d+(?:\.\d+)?万?次观看$/.test(text)) || '';
    const repostPermalink = Array.from(reposted?.closest('.retweet')?.querySelectorAll('a[href]') || [])
      .find((link) => {
        try {
          return /^\/\d+\/[A-Za-z0-9]+\/?$/.test(new URL(link.href).pathname);
        } catch (_error) {
          return false;
        }
      });

    return {
      id: url.pathname.split('/').filter(Boolean).pop(),
      uid,
      url: permalink.href,
      created_at: permalink.getAttribute('title') || cleanText(permalink.innerText),
      author_name: cleanText(authorLink?.innerText),
      author_avatar_url: authorAvatar?.currentSrc || authorAvatar?.src || '',
      source_label: Array.from(header?.querySelectorAll('div') || [])
        .map((element) => cleanText(element.innerText))
        .find((text) => /^来自\s/.test(text)) || '',
      text: cleanText(original?.innerText),
      repost_text: cleanText(reposted?.innerText),
      repost_created_at: repostPermalink?.getAttribute('title') || cleanText(repostPermalink?.innerText),
      repost_url: repostPermalink?.href || '',
      is_repost: hasRepost,
      edited: /已编辑/.test(headerText),
      truncated: /展开/.test(original?.innerText || ''),
      reposts: Number(metrics[0] || 0) || 0,
      comments: Number(metrics[1] || 0) || 0,
      likes: Number(metrics[2] || 0) || 0,
      repost_reposts: Number(repostMetrics[0] || 0) || 0,
      repost_comments: Number(repostMetrics[1] || 0) || 0,
      repost_likes: Number(repostMetrics[2] || 0) || 0,
      images: Array.from(new Set(images)),
      video_poster_url: videoPoster?.currentSrc || videoPoster?.src || '',
      video_url: videoLink?.href || '',
      video_duration: cleanText(article.querySelector('.vjs-duration-display')?.innerText),
      video_views: videoViews,
    };
  }).filter(Boolean);
})();
"""


EXPAND_VISIBLE_WEIBO_JS = r"""
return (() => {
  const buttons = Array.from(document.querySelectorAll('article span.expand'));
  for (const button of buttons) button.click();
  return buttons.length;
})();
"""


@dataclass(frozen=True)
class WeiboPost:
    id: str
    uid: str
    url: str
    created_at: str
    author_name: str = ""
    author_avatar_url: str = ""
    source_label: str = ""
    text: str = ""
    repost_text: str = ""
    repost_created_at: str = ""
    repost_url: str = ""
    is_repost: bool = False
    edited: bool = False
    truncated: bool = False
    reposts: int = 0
    comments: int = 0
    likes: int = 0
    repost_reposts: int = 0
    repost_comments: int = 0
    repost_likes: int = 0
    images: list[str] = field(default_factory=list)
    video_poster_url: str = ""
    video_url: str = ""
    video_duration: str = ""
    video_views: str = ""
    captured_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "WeiboPost":
        post_id = str(value.get("id") or "").strip()
        uid = str(value.get("uid") or "").strip()
        url = str(value.get("url") or "").strip()
        if not post_id or not uid or not url:
            raise ValueError("微博记录缺少 id、uid 或 url。")
        return cls(
            id=post_id,
            uid=uid,
            url=url,
            created_at=normalize_weibo_datetime(value.get("created_at")),
            author_name=normalize_weibo_text(value.get("author_name")),
            author_avatar_url=str(value.get("author_avatar_url") or "").strip(),
            source_label=normalize_weibo_text(value.get("source_label")),
            text=normalize_weibo_text(value.get("text")),
            repost_text=normalize_weibo_text(value.get("repost_text")),
            repost_created_at=normalize_weibo_datetime(value.get("repost_created_at")),
            repost_url=str(value.get("repost_url") or "").strip(),
            is_repost=bool(value.get("is_repost")),
            edited=bool(value.get("edited")),
            truncated=bool(value.get("truncated")),
            reposts=max(int(value.get("reposts") or 0), 0),
            comments=max(int(value.get("comments") or 0), 0),
            likes=max(int(value.get("likes") or 0), 0),
            repost_reposts=max(int(value.get("repost_reposts") or 0), 0),
            repost_comments=max(int(value.get("repost_comments") or 0), 0),
            repost_likes=max(int(value.get("repost_likes") or 0), 0),
            images=filter_weibo_media_urls(value.get("images") or []),
            video_poster_url=str(value.get("video_poster_url") or "").strip(),
            video_url=str(value.get("video_url") or "").strip(),
            video_duration=normalize_weibo_text(value.get("video_duration")),
            video_views=normalize_weibo_text(value.get("video_views")),
            captured_at=str(value.get("captured_at") or datetime.now().astimezone().isoformat()),
        )


def normalize_weibo_text(value: Any) -> str:
    text = str(value or "").replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_weibo_datetime(value: Any) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(
        r"(?P<year>\d{2}|\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\s+(?P<time>\d{2}:\d{2})",
        text,
    )
    if not match:
        return text
    year = int(match.group("year"))
    if year < 100:
        year += 2000
    return f"{year:04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d} {match.group('time')}"


def unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def is_weibo_avatar_url(value: Any) -> bool:
    url = str(value or "").strip()
    return bool(re.match(r"^https?://tva[x\d]*\d*\.sinaimg\.cn/crop\.", url, re.IGNORECASE))


def is_sina_blog_placeholder_url(value: Any) -> bool:
    """Identify the generic Sina Blog card logo, which is not post media."""
    url = str(value or "").strip()
    path = urlsplit(url).path
    filename = path.rsplit("/", 1)[-1]
    return (
        path.rstrip("/").endswith("/6c0c0bb3tdf7bdeae9c65")
        or filename.startswith(f"{SINA_BLOG_PLACEHOLDER_ASSET_STEM}.")
    )


def filter_weibo_media_urls(values: Iterable[Any]) -> list[str]:
    return [
        url
        for url in unique_strings(values)
        if not is_weibo_avatar_url(url) and not is_sina_blog_placeholder_url(url)
    ]


def _html_text(value: str) -> str:
    return escape(value).replace("\n", "<br/>")


def _metric_label(value: int) -> str:
    return str(value)


def _render_media_html(post: WeiboPost) -> str:
    if post.video_poster_url or post.video_url:
        target = escape(post.video_url or post.url, quote=True)
        duration = escape(post.video_duration or "", quote=True)
        views = escape(post.video_views or "", quote=True)
        poster = (
            f'<img alt="微博视频封面" loading="lazy" src="{escape(post.video_poster_url, quote=True)}" '
            'style="display:block;width:100%;height:100%;object-fit:cover;"/>'
            if post.video_poster_url
            else '<span style="position:absolute;inset:0;display:flex;align-items:center;'
            'justify-content:center;color:#bbb;font-size:15px;line-height:22px;">微博视频</span>'
        )
        return (
            f'<a class="weibo-video-card" href="{target}" '
            'style="position:relative;display:block;width:100%;max-width:542px;'
            'aspect-ratio:16/9;overflow:hidden;border-radius:8px;background:#000;line-height:0;">'
            f'{poster}'
            '<span aria-label="播放视频" style="position:absolute;left:50%;top:50%;'
            'transform:translate(-50%,-50%);color:rgba(255,255,255,.92);font-size:42px;'
            'line-height:1;text-shadow:0 1px 4px rgba(0,0,0,.45);">▶</span>'
            f'<span style="position:absolute;left:12px;bottom:10px;color:#fff;font-size:12px;'
            f'line-height:15px;text-shadow:0 1px 3px rgba(0,0,0,.9);">{views}</span>'
            f'<span style="position:absolute;right:12px;bottom:10px;color:#fff;font-size:12px;'
            f'line-height:15px;text-shadow:0 1px 3px rgba(0,0,0,.9);">{duration}</span>'
            '</a>'
        )
    if not post.images:
        return ""
    image_items = "".join(
        '<a href="{url}" style="display:block;aspect-ratio:1;overflow:hidden;border-radius:8px;line-height:0;">'
        '<img alt="微博配图" loading="lazy" src="{url}" '
        'style="display:block;width:100%;height:100%;object-fit:cover;"/></a>'.format(
            url=escape(url, quote=True)
        )
        for url in post.images
    )
    columns = min(len(post.images), 3)
    return (
        f'<div class="weibo-image-grid" style="display:grid;grid-template-columns:repeat({columns},1fr);'
        f'gap:4px;width:100%;max-width:410px;">{image_items}</div>'
    )


def render_weibo_entry_html(post: WeiboPost) -> str:
    """Render one post without repeating book-level author and source metadata."""
    meta = escape(post.created_at)
    if post.edited:
        meta += "（已编辑）"
    entry_meta = (
        '<div class="weibo-entry-meta" style="display:flex;align-items:center;'
        'justify-content:space-between;gap:8px 16px;flex-wrap:wrap;'
        'font-size:13px;line-height:18px;color:#939393;">'
        f'<span>{meta}</span>'
        '<span class="weibo-entry-stats" style="display:flex;align-items:center;'
        'gap:14px;white-space:nowrap;">'
        f'<a href="{escape(post.url, quote=True)}" style="color:#777;text-decoration:none;">原微博</a>'
        f'<span>转 {_metric_label(post.reposts)}</span>'
        f'<span>评 {_metric_label(post.comments)}</span>'
        f'<span>赞 {_metric_label(post.likes)}</span></span></div>'
    )
    original_text = (
        f'<div style="margin-top:3px;font-size:15px;line-height:24px;color:#333;">{_html_text(post.text)}</div>'
        if post.text
        else ""
    )
    media = _render_media_html(post)

    if post.is_repost:
        repost_text = _html_text(post.repost_text)
        repost_meta = ""
        if post.repost_created_at or post.repost_reposts or post.repost_comments or post.repost_likes:
            origin = escape(post.repost_url or post.url, quote=True)
            repost_meta = (
                '<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;'
                'margin-top:10px;font-size:13px;line-height:16px;color:#939393;">'
                f'<a href="{origin}" style="color:#939393;text-decoration:none;">'
                f'{escape(post.repost_created_at or "查看原帖")}</a>'
                '<span style="display:flex;gap:18px;white-space:nowrap;">'
                f'<span>转发 {_metric_label(post.repost_reposts)}</span>'
                f'<span>评论 {_metric_label(post.repost_comments)}</span>'
                f'<span>赞 {_metric_label(post.repost_likes)}</span></span></div>'
            )
        content = (
            f'{original_text}'
            '<div class="weibo-repost" style="box-sizing:border-box;margin:8px -18px 0;'
            'padding:8px 18px 12px;background:#f9f9f9;">'
            f'<div style="font-size:14px;line-height:24px;color:#333;">{repost_text}</div>'
            f'<div style="margin-top:8px;">{media}</div>{repost_meta}</div>'
        )
    else:
        content = f'{original_text}<div style="margin-top:8px;">{media}</div>' if media else original_text

    return (
        f'<section class="weibo-entry" id="weibo-{escape(post.id, quote=True)}" '
        'style="box-sizing:border-box;width:100%;max-width:640px;margin:0 auto 10px;'
        'overflow:hidden;border:1px solid #eef0f2;border-radius:4px;background:#fff;color:#333;">'
        '<div style="padding:14px 18px;">'
        f'{entry_meta}'
        f'<div class="weibo-main-column" style="width:auto;">{content}</div>'
        '</div></section>'
    )


class WeiboArchiveStore:
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
                CREATE TABLE IF NOT EXISTS weibo_posts (
                    id TEXT PRIMARY KEY,
                    uid TEXT NOT NULL,
                    url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    author_name TEXT NOT NULL DEFAULT '',
                    author_avatar_url TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    repost_text TEXT NOT NULL DEFAULT '',
                    repost_created_at TEXT NOT NULL DEFAULT '',
                    repost_url TEXT NOT NULL DEFAULT '',
                    is_repost INTEGER NOT NULL DEFAULT 0,
                    edited INTEGER NOT NULL DEFAULT 0,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    reposts INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    repost_reposts INTEGER NOT NULL DEFAULT 0,
                    repost_comments INTEGER NOT NULL DEFAULT 0,
                    repost_likes INTEGER NOT NULL DEFAULT 0,
                    images_json TEXT NOT NULL DEFAULT '[]',
                    video_poster_url TEXT NOT NULL DEFAULT '',
                    video_url TEXT NOT NULL DEFAULT '',
                    video_duration TEXT NOT NULL DEFAULT '',
                    video_views TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL
                )
                """
            )
            existing_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(weibo_posts)")
            }
            text_columns = (
                "author_name",
                "author_avatar_url",
                "source_label",
                "repost_created_at",
                "repost_url",
                "video_poster_url",
                "video_url",
                "video_duration",
                "video_views",
            )
            for column in text_columns:
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE weibo_posts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )
            integer_columns = ("repost_reposts", "repost_comments", "repost_likes")
            for column in integer_columns:
                if column not in existing_columns:
                    connection.execute(
                        f"ALTER TABLE weibo_posts ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_weibo_posts_uid_created_at "
                "ON weibo_posts(uid, created_at)"
            )

    def upsert_many(self, posts: Iterable[WeiboPost]) -> int:
        rows = list(posts)
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO weibo_posts (
                    id, uid, url, created_at, author_name, author_avatar_url, source_label,
                    text, repost_text, repost_created_at, repost_url, is_repost, edited,
                    truncated, reposts, comments, likes, repost_reposts, repost_comments,
                    repost_likes, images_json, video_poster_url,
                    video_url, video_duration, video_views, captured_at
                ) VALUES (
                    :id, :uid, :url, :created_at, :author_name, :author_avatar_url, :source_label,
                    :text, :repost_text, :repost_created_at, :repost_url, :is_repost, :edited,
                    :truncated, :reposts, :comments, :likes, :repost_reposts, :repost_comments,
                    :repost_likes, :images_json, :video_poster_url,
                    :video_url, :video_duration, :video_views, :captured_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    uid = excluded.uid,
                    url = excluded.url,
                    created_at = excluded.created_at,
                    author_name = CASE WHEN excluded.author_name != '' THEN excluded.author_name ELSE weibo_posts.author_name END,
                    author_avatar_url = CASE WHEN excluded.author_avatar_url != '' THEN excluded.author_avatar_url ELSE weibo_posts.author_avatar_url END,
                    source_label = CASE WHEN excluded.source_label != '' THEN excluded.source_label ELSE weibo_posts.source_label END,
                    text = CASE
                        WHEN excluded.truncated = 0 OR weibo_posts.text = '' THEN excluded.text
                        ELSE weibo_posts.text
                    END,
                    repost_text = CASE
                        WHEN excluded.repost_text != '' THEN excluded.repost_text
                        ELSE weibo_posts.repost_text
                    END,
                    repost_created_at = CASE WHEN excluded.repost_created_at != '' THEN excluded.repost_created_at ELSE weibo_posts.repost_created_at END,
                    repost_url = CASE WHEN excluded.repost_url != '' THEN excluded.repost_url ELSE weibo_posts.repost_url END,
                    is_repost = excluded.is_repost,
                    edited = excluded.edited,
                    truncated = MIN(weibo_posts.truncated, excluded.truncated),
                    reposts = excluded.reposts,
                    comments = excluded.comments,
                    likes = excluded.likes,
                    repost_reposts = excluded.repost_reposts,
                    repost_comments = excluded.repost_comments,
                    repost_likes = excluded.repost_likes,
                    images_json = CASE
                        WHEN excluded.images_json != '[]' THEN excluded.images_json
                        ELSE weibo_posts.images_json
                    END,
                    video_poster_url = CASE WHEN excluded.video_poster_url != '' THEN excluded.video_poster_url ELSE weibo_posts.video_poster_url END,
                    video_url = CASE WHEN excluded.video_url != '' THEN excluded.video_url ELSE weibo_posts.video_url END,
                    video_duration = CASE WHEN excluded.video_duration != '' THEN excluded.video_duration ELSE weibo_posts.video_duration END,
                    video_views = CASE WHEN excluded.video_views != '' THEN excluded.video_views ELSE weibo_posts.video_views END,
                    captured_at = excluded.captured_at
                """,
                [self._to_database_row(post) for post in rows],
            )
        return len(rows)

    @staticmethod
    def _to_database_row(post: WeiboPost) -> dict[str, Any]:
        row = asdict(post)
        row["is_repost"] = int(post.is_repost)
        row["edited"] = int(post.edited)
        row["truncated"] = int(post.truncated)
        row["images_json"] = json.dumps(post.images, ensure_ascii=False)
        row.pop("images")
        return row

    def count(self, uid: str | None = None) -> int:
        with self.connect() as connection:
            if uid:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM weibo_posts WHERE uid = ?", (uid,)
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS count FROM weibo_posts").fetchone()
        return int(row["count"] if row else 0)

    def replace_media(self, posts: Iterable[WeiboPost]) -> int:
        rows = list(posts)
        if not rows:
            return 0
        with self.connect() as connection:
            connection.executemany(
                "UPDATE weibo_posts SET images_json = ?, video_poster_url = ? WHERE id = ?",
                [
                    (json.dumps(post.images, ensure_ascii=False), post.video_poster_url, post.id)
                    for post in rows
                ],
            )
        return len(rows)

    def list_posts(self, *, uid: str, oldest_first: bool = True) -> list[WeiboPost]:
        direction = "ASC" if oldest_first else "DESC"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM weibo_posts WHERE uid = ? ORDER BY created_at {direction}, id {direction}",
                (uid,),
            ).fetchall()
        return [
            WeiboPost(
                id=row["id"],
                uid=row["uid"],
                url=row["url"],
                created_at=row["created_at"],
                author_name=row["author_name"],
                author_avatar_url=row["author_avatar_url"],
                source_label=row["source_label"],
                text=row["text"],
                repost_text=row["repost_text"],
                repost_created_at=row["repost_created_at"],
                repost_url=row["repost_url"],
                is_repost=bool(row["is_repost"]),
                edited=bool(row["edited"]),
                truncated=bool(row["truncated"]),
                reposts=row["reposts"],
                comments=row["comments"],
                likes=row["likes"],
                repost_reposts=row["repost_reposts"],
                repost_comments=row["repost_comments"],
                repost_likes=row["repost_likes"],
                images=json.loads(row["images_json"] or "[]"),
                video_poster_url=row["video_poster_url"],
                video_url=row["video_url"],
                video_duration=row["video_duration"],
                video_views=row["video_views"],
                captured_at=row["captured_at"],
            )
            for row in rows
        ]


def render_markdown(posts: Iterable[WeiboPost], *, title: str) -> str:
    rows = list(posts)
    lines = [f"# {title}", "", f"共收录 {len(rows)} 条微博。", ""]
    for post in rows:
        heading = post.created_at or post.id
        if post.edited:
            heading += "（已编辑）"
        lines.extend([f"## {heading}", ""])
        if post.text:
            lines.extend([post.text, ""])
        if post.is_repost and post.repost_text:
            lines.extend(
                ["> 转发内容", ">", *[f"> {line}" for line in post.repost_text.splitlines()], ""]
            )
        for image_url in post.images:
            lines.extend([f"![微博图片]({image_url})", ""])
        if post.video_poster_url:
            video_target = post.video_url or post.url
            video_note = " · ".join(
                value for value in (post.video_duration, post.video_views) if value
            )
            lines.extend(
                [
                    f"[![微博视频封面]({post.video_poster_url})]({video_target})",
                    *( [video_note] if video_note else [] ),
                    "",
                ]
            )
        if post.truncated:
            lines.extend(["> 此条在列表页仍为截断状态，需后续补采全文。", ""])
        lines.extend([f"[查看原微博]({post.url})", "", "---", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_yearly_book_payload(
    posts: Iterable[WeiboPost],
    *,
    topic_id: int,
    title: str,
    author: str,
    source_url: str,
    imported_at: float | None = None,
) -> dict[str, Any]:
    """Build the dynamic HTML-book payload with one article per year."""
    ordered = sorted(posts, key=lambda post: (post.created_at, post.id))
    years: dict[str, dict[str, list[WeiboPost]]] = {}
    for post in ordered:
        month = post.created_at[:7]
        years.setdefault(month[:4], {}).setdefault(month, []).append(post)

    articles: list[str] = []
    toc: list[dict[str, Any]] = []
    # Recent years are easiest to reach from the book directory, while entries
    # inside each year retain their natural chronological reading order.
    for year in reversed(years):
        months = years[year]
        year_posts = [post for month_posts in months.values() for post in month_posts]
        anchor = f"year-{year}"
        heading = f"{year}年（{len(year_posts)}则）"
        month_sections: list[str] = []
        for month, month_posts in months.items():
            month_number = int(month.split("-", 1)[1])
            month_sections.append(
                f'<h2 id="month-{month}" style="max-width:640px;margin:26px auto 12px;'
                f'font-size:19px;line-height:1.4;color:#374151;">{month_number}月（{len(month_posts)}则）</h2>'
                + "".join(render_weibo_entry_html(post) for post in month_posts)
            )
        articles.append(
            f'<article data-article-id="{anchor}" '
            'style="box-sizing:border-box;width:100%;max-width:680px;margin:0 auto 34px;">'
            f'<h1 id="{anchor}" style="max-width:640px;margin:0 auto 18px;'
            f'font-size:26px;line-height:1.35;color:#1f2937;">{heading}</h1>'
            + "".join(month_sections)
            + "</article>"
        )
        toc.append({
            "title": heading,
            "number": "",
            "level": 2,
            "anchor": anchor,
            "source_post_number": None,
            "inferred": False,
        })

    content_html = "".join(articles)
    revision = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
    page_text_length = sum(len(post.text) + len(post.repost_text) for post in ordered)
    return {
        "topic_id": topic_id,
        "title": title,
        "author": author,
        "source_url": source_url,
        "content_html": content_html,
        "content_markdown": "",
        "toc": toc,
        "revision": revision,
        "post_count": len(ordered),
        "selected_reply_count": 0,
        "imported_at": imported_at if imported_at is not None else time.time(),
        "estimated_page_count": dynamic_book_page_count(page_text_length),
    }


def write_yearly_book_json(
    store: WeiboArchiveStore,
    *,
    uid: str,
    path: Path,
    title: str,
    author: str,
) -> Path:
    payload = build_yearly_book_payload(
        store.list_posts(uid=uid),
        topic_id=int(uid),
        title=title,
        author=author,
        source_url=WEIBO_PROFILE_URL_TEMPLATE.format(uid=uid),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{time.time_ns()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def _image_extension(content: bytes) -> str:
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return ""


def prefer_original_sina_image_url(url: str) -> str:
    """Replace Weibo thumbnail renditions with the original-quality endpoint."""
    parsed = urlsplit(str(url))
    hostname = (parsed.hostname or "").lower()
    if not re.fullmatch(r"(?:wx|ww)\d+\.sinaimg\.cn", hostname):
        return str(url)
    parts = parsed.path.split("/")
    if len(parts) < 3 or parts[1] not in {
        "orj360",
        "orj480",
        "orj720",
        "orj1080",
        "wap720",
        "thumb180",
        "thumbnail",
        "bmiddle",
        "mw690",
        "mw1024",
    }:
        return str(url)
    parts[1] = "large"
    return urlunsplit(parsed._replace(path="/".join(parts)))


def cache_weibo_media(
    store: WeiboArchiveStore,
    *,
    uid: str,
    directory: Path,
    url_prefix: str,
    max_workers: int = 8,
    fetcher: Callable[[str, str], bytes] | None = None,
) -> dict[str, int]:
    """Cache referenced media locally so archived books do not rely on hotlinked images."""
    posts = store.list_posts(uid=uid)
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / "manifest.json"
    quality_manifest_path = directory / "quality_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    try:
        quality_manifest = json.loads(quality_manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        quality_manifest = {}
    if not isinstance(quality_manifest, dict):
        quality_manifest = {}

    references: dict[str, str] = {}
    for post in posts:
        for url in [*post.images, post.video_poster_url]:
            if str(url).startswith(("http://", "https://")):
                references.setdefault(str(url), post.url)

    cached: dict[str, str] = {}
    for source_url, local_url in manifest.items():
        filename = str(local_url).rsplit("/", 1)[-1]
        if filename and (directory / filename).is_file():
            cached[str(source_url)] = str(local_url)

    def default_fetcher(source_url: str, referer: str) -> bytes:
        response = requests.get(
            source_url,
            headers={"Referer": referer},
            impersonate="chrome",
            timeout=25,
            allow_redirects=True,
        )
        response.raise_for_status()
        return bytes(response.content)

    retrieve = fetcher or default_fetcher

    def download(
        source_url: str,
        referer: str,
        existing_local_url: str = "",
    ) -> tuple[str, str, str, bool] | None:
        preferred_url = prefer_original_sina_image_url(source_url)
        fetched_url = preferred_url
        try:
            content = retrieve(preferred_url, referer)
        except Exception:
            if preferred_url == source_url:
                return None
            fetched_url = source_url
            try:
                content = retrieve(source_url, referer)
            except Exception:
                return None
        extension = _image_extension(content)
        if not extension:
            return None
        filename = (
            str(existing_local_url).rsplit("/", 1)[-1]
            if existing_local_url
            else hashlib.sha256(source_url.encode("utf-8")).hexdigest() + extension
        )
        target = directory / filename
        upgraded = preferred_url != source_url and fetched_url == preferred_url
        if not target.exists() or upgraded:
            temporary = target.with_suffix(target.suffix + f".{time.time_ns()}.tmp")
            temporary.write_bytes(content)
            temporary.replace(target)
        return (
            source_url,
            existing_local_url or f"{url_prefix.rstrip('/')}/{filename}",
            fetched_url,
            upgraded,
        )

    pending: list[tuple[str, str, str]] = [
        (url, referer, "") for url, referer in references.items() if url not in cached
    ]
    queued = {source_url for source_url, _referer, _local_url in pending}
    profile_referer = WEIBO_PROFILE_URL_TEMPLATE.format(uid=uid)
    for source_url, local_url in cached.items():
        preferred_url = prefer_original_sina_image_url(source_url)
        if (
            source_url not in queued
            and preferred_url != source_url
            and quality_manifest.get(source_url) != preferred_url
        ):
            pending.append((source_url, profile_referer, local_url))
            queued.add(source_url)

    upgraded_count = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = [
            executor.submit(download, url, referer, local_url)
            for url, referer, local_url in pending
        ]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                cached[result[0]] = result[1]
                if result[3]:
                    quality_manifest[result[0]] = result[2]
                    upgraded_count += 1

    def cached_or_existing(url: str) -> str:
        if not url.startswith(("http://", "https://")):
            return url
        return cached.get(url, "")

    updated_posts = []
    for post in posts:
        images = [cached_or_existing(url) for url in post.images]
        updated_posts.append(replace(
            post,
            images=[url for url in images if url],
            video_poster_url=cached_or_existing(post.video_poster_url),
        ))
    store.replace_media(updated_posts)
    manifest_path.write_text(
        json.dumps(cached, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    quality_manifest_path.write_text(
        json.dumps(quality_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    cached_reference_count = sum(1 for url in references if url in cached)
    return {
        "referenced": len(references),
        "cached": cached_reference_count,
        "failed": len(references) - cached_reference_count,
        "upgraded": upgraded_count,
    }


def export_markdown(store: WeiboArchiveStore, *, uid: str, path: Path, title: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_markdown(store.list_posts(uid=uid, oldest_first=True), title=title),
        encoding="utf-8",
    )
    return path


def load_batch_jsonl(path: Path) -> list[WeiboPost]:
    posts_by_id: dict[str, WeiboPost] = {}
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{path} 第 {line_number} 行含有非对象微博记录。")
            post = WeiboPost.from_mapping(row)
            previous = posts_by_id.get(post.id)
            if previous is None or (previous.truncated and not post.truncated):
                posts_by_id[post.id] = post
    return list(posts_by_id.values())


def extract_visible_posts(tab: Any, *, uid: str) -> list[WeiboPost]:
    raw_rows = tab.run_js(WEIBO_ARTICLE_EXTRACTOR_JS, uid) or []
    return [WeiboPost.from_mapping(row) for row in raw_rows]


def assert_weibo_page_ready(tab: Any, *, uid: str) -> None:
    title = str(getattr(tab, "title", "") or "")
    url = str(getattr(tab, "url", "") or "")
    html_sample = str(getattr(tab, "html", "") or "")[:5000]
    observed = "\n".join((title, url, html_sample))
    if any(marker in observed for marker in LOGIN_OR_RISK_MARKERS):
        raise RuntimeError(f"微博页面需要人工处理登录或验证：{title} | {url}")
    if uid not in url:
        raise RuntimeError(f"微博页面没有进入目标账号 {uid}：{url}")


def wait_for_visible_tail_change(
    tab: Any,
    *,
    uid: str,
    previous_tail_id: str,
    timeout_seconds: float = 8.0,
) -> list[WeiboPost]:
    deadline = time.monotonic() + timeout_seconds
    latest: list[WeiboPost] = []
    while time.monotonic() < deadline:
        latest = extract_visible_posts(tab, uid=uid)
        if latest and latest[-1].id != previous_tail_id:
            return latest
        time.sleep(0.25)
    return latest


def crawl_weibo_profile(
    tab: Any,
    *,
    uid: str,
    store: WeiboArchiveStore,
    max_posts: int = 0,
    max_stagnant_rounds: int = 5,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    profile_url = WEIBO_PROFILE_URL_TEMPLATE.format(uid=uid)
    tab.get(profile_url)
    assert_weibo_page_ready(tab, uid=uid)

    stagnant_rounds = 0
    rounds = 0
    seen_ids: set[str] = set()
    while stagnant_rounds < max_stagnant_rounds:
        tab.run_js(EXPAND_VISIBLE_WEIBO_JS)
        posts = extract_visible_posts(tab, uid=uid)
        if not posts:
            raise RuntimeError("目标主页已打开，但当前没有识别到微博卡片。")
        store.upsert_many(posts)
        new_ids = {post.id for post in posts} - seen_ids
        seen_ids.update(post.id for post in posts)
        stagnant_rounds = 0 if new_ids else stagnant_rounds + 1
        rounds += 1
        archived_count = store.count(uid)
        log(
            f"第 {rounds} 轮：可见 {len(posts)} 条，新增 {len(new_ids)} 条，"
            f"归档共 {archived_count} 条。"
        )
        if max_posts > 0 and archived_count >= max_posts:
            break

        previous_tail_id = posts[-1].id
        tab.scroll.to_bottom()
        next_posts = wait_for_visible_tail_change(
            tab,
            uid=uid,
            previous_tail_id=previous_tail_id,
        )
        if not next_posts or next_posts[-1].id == previous_tail_id:
            stagnant_rounds += 1

    return {
        "uid": uid,
        "rounds": rounds,
        "post_count": store.count(uid),
        "stagnant_rounds": stagnant_rounds,
        "database": str(store.path),
    }
