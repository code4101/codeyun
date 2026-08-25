from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from typing import Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdown_it import MarkdownIt
import requests
from sqlmodel import Session, select

from backend.core.library.dynamic_book_pagination import dynamic_book_html_page_count
from backend.core.library.linux_do_book import LinuxDoBookDocument, LinuxDoTocItem
from backend.core.notes.weekly_scheduler import (
    RUANYF_WEEKLY_BRANCH,
    RUANYF_WEEKLY_REPO_NAME,
    RUANYF_WEEKLY_REPO_OWNER,
    RUANYF_WEEKLY_TIMEZONE,
    RuanyfWeeklyIssue,
    collect_ruanyf_weekly_local_state,
    fetch_latest_ruanyf_weekly_issue,
    fetch_ruanyf_weekly_publication,
)
from backend.core.settings import get_settings
from backend.models import (
    LibraryBookAsset,
    LibraryBookPlacement,
    PdfBookshelfPlacement,
    PdfLibraryBookshelf,
    User,
)


RUANYF_WEEKLY_BOOK_START_ISSUE = 401
RUANYF_WEEKLY_BOOK_SOURCE_KIND = "ruanyf-weekly-book:401"
RUANYF_WEEKLY_BOOK_RESOURCE_TYPE = "linux-do-book"
RUANYF_WEEKLY_BOOK_SOURCE_URL = (
    f"https://github.com/{RUANYF_WEEKLY_REPO_OWNER}/{RUANYF_WEEKLY_REPO_NAME}"
)
RUANYF_WEEKLY_RAW_BASE_URL = (
    f"https://raw.githubusercontent.com/{RUANYF_WEEKLY_REPO_OWNER}/"
    f"{RUANYF_WEEKLY_REPO_NAME}/{RUANYF_WEEKLY_BRANCH}/"
)
RUANYF_WEEKLY_BLOB_BASE_URL = (
    f"https://github.com/{RUANYF_WEEKLY_REPO_OWNER}/"
    f"{RUANYF_WEEKLY_REPO_NAME}/blob/{RUANYF_WEEKLY_BRANCH}/"
)
WEEKLY_HEADING_RE = re.compile(
    r"^\s*#\s*科技爱好者周刊[（(]第\s*\d+\s*期[）)]\s*[:：]\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)
WEEKLY_BOOK_TITLE_RE = re.compile(r"科技爱好者周刊\s+\d+至\d+\s+期")
WEEKLY_CHAPTER_TITLE_RE = re.compile(
    r"^\s*(?P<number>\d+)\s+(?P<title>.*?)(?:（\d{4}-\d{2}-\d{2}）)?\s*$"
)
MARKDOWN_RENDERER = MarkdownIt("commonmark", {"html": True, "linkify": True})


@dataclass(frozen=True)
class RuanyfWeeklyBookResult:
    status: str
    book_id: str | None = None
    issue_number: int | None = None
    added_issue_numbers: tuple[int, ...] = ()
    message: str = ""
    next_run_at: str | None = None


def fetch_ruanyf_weekly_markdown(issue_number: int) -> str:
    path = f"docs/issue-{int(issue_number)}.md"
    response = requests.get(
        urljoin(RUANYF_WEEKLY_RAW_BASE_URL, path),
        headers={"User-Agent": "CodeYun ruanyf-weekly-book-updater"},
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def fetch_ruanyf_weekly_publication_date(issue_number: int) -> str:
    issue = RuanyfWeeklyIssue(
        number=int(issue_number),
        title="",
        path=f"docs/issue-{int(issue_number)}.md",
    )
    publication = fetch_ruanyf_weekly_publication(issue)
    if publication is None:
        raise RuntimeError(f"第 {int(issue_number)} 期没有找到 GitHub 发布时间")
    return publication.published_at.astimezone(RUANYF_WEEKLY_TIMEZONE).date().isoformat()


def update_ruanyf_weekly_book(
    session: Session,
    *,
    owner_user_id: int | None = None,
    latest_issue: RuanyfWeeklyIssue | None = None,
    fetch_markdown: Callable[[int], str] = fetch_ruanyf_weekly_markdown,
    fetch_publication_date: Callable[[int], str] = fetch_ruanyf_weekly_publication_date,
) -> RuanyfWeeklyBookResult:
    try:
        remote_latest = latest_issue or fetch_latest_ruanyf_weekly_issue()
    except Exception as exc:
        return RuanyfWeeklyBookResult(status="source_unavailable", message=str(exc))
    if remote_latest is None or remote_latest.number < RUANYF_WEEKLY_BOOK_START_ISSUE:
        return RuanyfWeeklyBookResult(status="latest_issue_not_found")

    asset = _find_existing_asset(session, owner_user_id)
    target_user_id = owner_user_id or (int(asset.owner_user_id) if asset is not None else None)
    if target_user_id is None:
        target_user_id = collect_ruanyf_weekly_local_state(session).target_user_id
    if target_user_id is None or session.get(User, int(target_user_id)) is None:
        return RuanyfWeeklyBookResult(
            status="no_target_user",
            issue_number=remote_latest.number,
            message="No existing weekly-book owner or ruanyf weekly note owner was found",
        )

    if asset is None:
        asset = _find_existing_asset(session, int(target_user_id))
    existing_document = _read_document(asset) if asset is not None else None
    existing_latest = _existing_latest_issue(asset, existing_document)
    if existing_latest >= remote_latest.number:
        return RuanyfWeeklyBookResult(
            status="up_to_date",
            book_id=asset.id if asset is not None else None,
            issue_number=remote_latest.number,
        )

    first_missing = max(RUANYF_WEEKLY_BOOK_START_ISSUE, existing_latest + 1)
    try:
        new_chapters = [
            _build_chapter(
                issue_number,
                fetch_markdown(issue_number),
                fetch_publication_date(issue_number),
            )
            for issue_number in range(first_missing, remote_latest.number + 1)
        ]
    except Exception as exc:
        return RuanyfWeeklyBookResult(
            status="source_unavailable",
            book_id=asset.id if asset is not None else None,
            issue_number=remote_latest.number,
            message=str(exc),
        )

    now = time.time()
    if existing_document is None or existing_latest < RUANYF_WEEKLY_BOOK_START_ISSUE:
        content_html = "".join(chapter.html for chapter in new_chapters)
        content_markdown = "\n\n".join(chapter.markdown for chapter in new_chapters).strip() + "\n"
        toc = [chapter.toc_item for chapter in new_chapters]
        imported_at = now
    else:
        content_html = existing_document.content_html + "".join(chapter.html for chapter in new_chapters)
        content_markdown = (
            existing_document.content_markdown.rstrip()
            + "\n\n"
            + "\n\n".join(chapter.markdown for chapter in new_chapters).strip()
            + "\n"
        )
        toc = [*existing_document.toc, *(chapter.toc_item for chapter in new_chapters)]
        imported_at = existing_document.imported_at

    title = _book_title(remote_latest.number)
    revision = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
    topic_id = _topic_id(int(target_user_id))
    document = LinuxDoBookDocument(
        topic_id=topic_id,
        title=title,
        author="阮一峰",
        source_url=RUANYF_WEEKLY_BOOK_SOURCE_URL,
        content_html=content_html,
        content_markdown=content_markdown,
        toc=toc,
        revision=revision,
        post_count=len(toc),
        selected_reply_count=0,
        imported_at=imported_at,
        estimated_page_count=dynamic_book_html_page_count(content_html),
    )

    created = asset is None
    if asset is None:
        asset = LibraryBookAsset(
            id=_asset_id(int(target_user_id)),
            resource_type=RUANYF_WEEKLY_BOOK_RESOURCE_TYPE,
            owner_user_id=int(target_user_id),
            source_kind=RUANYF_WEEKLY_BOOK_SOURCE_KIND,
            created_at=now,
        )
    asset.title = title
    asset.author = "阮一峰"
    asset.cover_color = "#315f53"
    asset.metadata_json = {
        **dict(asset.metadata_json or {}),
        "storage_version": 1,
        "topic_id": topic_id,
        "book_kind": "ebook",
        "format": "html",
        "dynamic": True,
        "start_issue": RUANYF_WEEKLY_BOOK_START_ISSUE,
        "latest_issue": remote_latest.number,
        "source_url": RUANYF_WEEKLY_BOOK_SOURCE_URL,
        "revision": revision,
        "toc_count": len(toc),
        "post_count": len(toc),
        "estimated_page_count": document.estimated_page_count,
        "imported_at": imported_at,
        "updated_at": now,
    }
    asset.updated_at = now
    session.add(asset)
    _ensure_placement(session, asset, int(target_user_id), now)
    _write_document(int(target_user_id), document)
    session.commit()

    return RuanyfWeeklyBookResult(
        status="created" if created else "updated",
        book_id=asset.id,
        issue_number=remote_latest.number,
        added_issue_numbers=tuple(chapter.issue_number for chapter in new_chapters),
    )


@dataclass(frozen=True)
class _WeeklyChapter:
    issue_number: int
    html: str
    markdown: str
    toc_item: LinuxDoTocItem


def _build_chapter(
    issue_number: int,
    markdown_source: str,
    publication_date: str,
) -> _WeeklyChapter:
    title_match = WEEKLY_HEADING_RE.search(markdown_source)
    subtitle = (
        title_match.group("title").strip()
        if title_match
        else f"科技爱好者周刊第 {int(issue_number)} 期"
    )
    chapter_title = _dated_chapter_title(
        issue_number,
        subtitle,
        publication_date,
    )
    body_markdown = WEEKLY_HEADING_RE.sub("", markdown_source, count=1).strip()
    soup = BeautifulSoup(MARKDOWN_RENDERER.render(body_markdown), "html.parser")
    source_path = f"docs/issue-{int(issue_number)}.md"
    for node in soup.find_all(["a", "img"]):
        attribute = "href" if node.name == "a" else "src"
        value = str(node.get(attribute) or "").strip()
        if not value or urlsplit(value).scheme or value.startswith(("#", "data:", "mailto:")):
            continue
        base_url = RUANYF_WEEKLY_BLOB_BASE_URL if node.name == "a" else RUANYF_WEEKLY_RAW_BASE_URL
        node[attribute] = urljoin(base_url + source_path, value)

    anchor = f"issue-{int(issue_number)}"
    source_url = urljoin(RUANYF_WEEKLY_BLOB_BASE_URL, source_path)
    article_html = (
        f'<article data-article-id="{anchor}">'
        f'<h1 id="{anchor}">{html.escape(chapter_title)}</h1>'
        f'<p class="imported-book-source"><a href="{html.escape(source_url, quote=True)}" '
        'target="_blank" rel="noopener noreferrer">查看本期原文</a></p>'
        f"{''.join(map(str, soup.contents))}</article>"
    )
    chapter_markdown = f"# {chapter_title}\n\n[查看本期原文]({source_url})\n\n{body_markdown}"
    return _WeeklyChapter(
        issue_number=int(issue_number),
        html=article_html,
        markdown=chapter_markdown,
        toc_item=LinuxDoTocItem(
            title=chapter_title,
            number="",
            level=1,
            anchor=anchor,
            source_post_number=int(issue_number),
        ),
    )


def _dated_chapter_title(
    issue_number: int,
    subtitle: str,
    publication_date: str,
) -> str:
    normalized_date = str(publication_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_date):
        raise ValueError(f"第 {int(issue_number)} 期发布日期无效：{publication_date}")
    return f"{int(issue_number)} {str(subtitle).strip()}（{normalized_date}）"


def backfill_ruanyf_weekly_publication_dates(
    session: Session,
    publication_dates: dict[int, str],
) -> dict[str, tuple[int, ...]]:
    """Write publication dates into TOC entries and chapter headings of local weekly books."""
    assets = session.exec(
        select(LibraryBookAsset).where(
            LibraryBookAsset.title.contains("科技爱好者周刊")
        )
    ).all()
    changed_by_asset: dict[str, tuple[int, ...]] = {}
    now = time.time()
    for asset in assets:
        if not WEEKLY_BOOK_TITLE_RE.search(asset.title):
            continue
        document = _read_document(asset)
        if document is None:
            continue
        soup = BeautifulSoup(document.content_html, "html.parser")
        markdown = document.content_markdown
        changed_issue_numbers: list[int] = []
        stored_dates = dict((asset.metadata_json or {}).get("issue_publication_dates") or {})
        for item in document.toc:
            match = WEEKLY_CHAPTER_TITLE_RE.fullmatch(item.title)
            if match is None:
                continue
            issue_number = int(match.group("number"))
            publication_date = publication_dates.get(issue_number)
            if publication_date is None:
                continue
            new_title = _dated_chapter_title(
                issue_number,
                match.group("title"),
                publication_date,
            )
            stored_dates[str(issue_number)] = publication_date
            if item.title == new_title:
                continue
            old_title = item.title
            item.title = new_title
            heading = soup.find(id=item.anchor)
            if isinstance(heading, Tag):
                heading.clear()
                heading.string = new_title
            markdown = re.sub(
                rf"(?m)^#\s+{re.escape(old_title)}\s*$",
                f"# {new_title}",
                markdown,
                count=1,
            )
            changed_issue_numbers.append(issue_number)
        if not changed_issue_numbers:
            continue
        document.content_html = str(soup)
        document.content_markdown = markdown
        document.revision = hashlib.sha256(document.content_html.encode("utf-8")).hexdigest()
        document.estimated_page_count = dynamic_book_html_page_count(document.content_html)
        metadata = dict(asset.metadata_json or {})
        metadata["revision"] = document.revision
        metadata["estimated_page_count"] = document.estimated_page_count
        metadata["issue_publication_dates"] = stored_dates
        metadata["publication_dates_updated_at"] = now
        asset.metadata_json = metadata
        session.add(asset)
        _write_document(asset.owner_user_id, document)
        changed_by_asset[asset.id] = tuple(changed_issue_numbers)
    session.commit()
    return changed_by_asset


def _find_existing_asset(
    session: Session,
    owner_user_id: int | None,
) -> LibraryBookAsset | None:
    statement = select(LibraryBookAsset).where(
        LibraryBookAsset.source_kind == RUANYF_WEEKLY_BOOK_SOURCE_KIND
    )
    if owner_user_id is not None:
        statement = statement.where(LibraryBookAsset.owner_user_id == int(owner_user_id))
    return session.exec(statement.order_by(LibraryBookAsset.updated_at.desc())).first()


def _existing_latest_issue(
    asset: LibraryBookAsset | None,
    document: LinuxDoBookDocument | None,
) -> int:
    if asset is None or document is None:
        return RUANYF_WEEKLY_BOOK_START_ISSUE - 1
    metadata_latest = int((asset.metadata_json or {}).get("latest_issue") or 0)
    toc_latest = max(
        (
            int(match.group(1))
            for item in document.toc
            if (match := re.fullmatch(r"issue-(\d+)", item.anchor))
        ),
        default=0,
    )
    return min(metadata_latest, toc_latest) if metadata_latest and toc_latest else 0


def _book_title(latest_issue: int) -> str:
    return f"科技爱好者周刊 {RUANYF_WEEKLY_BOOK_START_ISSUE}至{int(latest_issue)} 期"


def _asset_id(owner_user_id: int) -> str:
    return f"ruanyf-weekly:{int(owner_user_id)}:{RUANYF_WEEKLY_BOOK_START_ISSUE}"


def _topic_id(owner_user_id: int) -> int:
    digest = hashlib.sha256(_asset_id(owner_user_id).encode("utf-8")).hexdigest()
    return -int(digest[:14], 16)


def _storage_path(owner_user_id: int, topic_id: int) -> Path:
    return (
        get_settings().data_dir
        / "library-books"
        / f"user_{int(owner_user_id)}"
        / "linux-do"
        / str(int(topic_id))
        / "book.json"
    )


def _write_document(owner_user_id: int, document: LinuxDoBookDocument) -> None:
    path = _storage_path(owner_user_id, document.topic_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_document(asset: LibraryBookAsset | None) -> LinuxDoBookDocument | None:
    if asset is None:
        return None
    topic_id = int((asset.metadata_json or {}).get("topic_id") or 0)
    try:
        payload = json.loads(
            _storage_path(asset.owner_user_id, topic_id).read_text(encoding="utf-8")
        )
        payload["toc"] = [LinuxDoTocItem(**item) for item in payload.get("toc") or []]
        return LinuxDoBookDocument(**payload)
    except (OSError, ValueError, TypeError, KeyError):
        return None


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

    shelf = session.exec(
        select(PdfLibraryBookshelf)
        .where(PdfLibraryBookshelf.user_id == int(user_id))
        .order_by(PdfLibraryBookshelf.sort_index, PdfLibraryBookshelf.created_at)
    ).first()
    if shelf is None:
        shelves = [
            PdfLibraryBookshelf(
                user_id=int(user_id),
                name=name,
                sort_index=sort_index,
                created_at=now,
                updated_at=now,
            )
            for sort_index, name in enumerate(("1", "2", "4", "5"))
        ]
        session.add_all(shelves)
        session.flush()
        shelf = shelves[0]

    positions = [
        *session.exec(
            select(PdfBookshelfPlacement.position_index)
            .where(PdfBookshelfPlacement.user_id == int(user_id))
            .where(PdfBookshelfPlacement.bookshelf_id == shelf.id)
            .where(PdfBookshelfPlacement.shelf_index == 0)
        ).all(),
        *session.exec(
            select(LibraryBookPlacement.position_index)
            .where(LibraryBookPlacement.user_id == int(user_id))
            .where(LibraryBookPlacement.bookshelf_id == shelf.id)
            .where(LibraryBookPlacement.shelf_index == 0)
        ).all(),
    ]
    session.add(
        LibraryBookPlacement(
            book_asset_id=asset.id,
            user_id=int(user_id),
            bookshelf_id=shelf.id,
            shelf_index=0,
            position_index=max([int(value) for value in positions], default=-1) + 1,
            orientation="spine_vertical",
            created_at=now,
            updated_at=now,
        )
    )
