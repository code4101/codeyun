from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag
from sqlmodel import Session, select

from backend.core.jobs.local_runtime import submit_local_job_once
from backend.core.library.dynamic_book_pagination import dynamic_book_html_page_count
from backend.core.library.linux_do_book import LinuxDoBookDocument, LinuxDoTocItem
from backend.core.settings import get_settings
from backend.models import LibraryBookAsset, NoteNode


RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY = "ruanyf_weekly_excerpt_book"
RUANYF_WEEKLY_EXCERPT_BOOK_RUN_TIME = "00:00"
RUANYF_WEEKLY_EXCERPT_BOOK_WEEKDAYS = (7,)

BOOK_TITLE_RE = re.compile(
    r"^我的科技周刊摘抄\s*(?P<first>\d+)\s*至\s*(?P<last>\d+)\s*期$"
)
NOTE_TITLE_RE = re.compile(
    r"^科技周刊(?:第)*\s*(?P<number>\d+)\s*期\s*[:：]\s*(?P<title>.+?)\s*$"
)
TOC_ISSUE_RE = re.compile(r"^\s*(?P<number>\d+)\s+(?P<title>.+?)\s*$")
ISSUE_SOURCE_RE = re.compile(
    r"https?://github\.com/ruanyf/weekly/(?:blob|raw)/[^\"'\s<]+/docs/issue-(?P<number>\d+)\.md"
)
URL_RE = re.compile(r"https?://\S+")
DISPLAY_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class RuanyfWeeklyExcerptBookResult:
    status: str
    updated_book_ids: tuple[str, ...] = ()
    added_issue_numbers: tuple[int, ...] = ()
    dated_issue_numbers: tuple[int, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class _Excerpt:
    issue_number: int
    title: str
    content_html: str
    note_id: str
    source_url: str
    excerpt_date: str


@dataclass(frozen=True)
class _Chapter:
    issue_number: int
    html: str
    markdown: str
    toc_item: LinuxDoTocItem


def update_ruanyf_weekly_excerpt_books(
    session: Session,
    *,
    owner_user_id: int | None = None,
) -> RuanyfWeeklyExcerptBookResult:
    statement = select(LibraryBookAsset).where(
        LibraryBookAsset.title.contains("我的科技周刊摘抄")
    )
    if owner_user_id is not None:
        statement = statement.where(LibraryBookAsset.owner_user_id == int(owner_user_id))
    assets = [
        asset
        for asset in session.exec(statement.order_by(LibraryBookAsset.updated_at.desc())).all()
        if BOOK_TITLE_RE.fullmatch(asset.title.strip())
    ]
    if not assets:
        return RuanyfWeeklyExcerptBookResult(status="book_not_found")

    updated_book_ids: list[str] = []
    added_issue_numbers: set[int] = set()
    dated_issue_numbers: set[int] = set()
    unreadable_book_ids: list[str] = []
    for asset in assets:
        document = _read_document(asset)
        if document is None:
            unreadable_book_ids.append(asset.id)
            continue
        excerpts = _collect_excerpts(session, asset.owner_user_id)
        added, dated = _update_asset_from_excerpts(session, asset, document, excerpts)
        if added or dated:
            updated_book_ids.append(asset.id)
            added_issue_numbers.update(added)
            dated_issue_numbers.update(dated)

    if updated_book_ids:
        session.commit()
        return RuanyfWeeklyExcerptBookResult(
            status="updated",
            updated_book_ids=tuple(updated_book_ids),
            added_issue_numbers=tuple(sorted(added_issue_numbers)),
            dated_issue_numbers=tuple(sorted(dated_issue_numbers)),
        )
    if unreadable_book_ids and len(unreadable_book_ids) == len(assets):
        return RuanyfWeeklyExcerptBookResult(
            status="book_unreadable",
            message=",".join(unreadable_book_ids),
        )
    return RuanyfWeeklyExcerptBookResult(status="up_to_date")


def run_ruanyf_weekly_excerpt_book_job() -> RuanyfWeeklyExcerptBookResult:
    from backend.db import engine

    with Session(engine) as session:
        result = update_ruanyf_weekly_excerpt_books(session)
    print(
        "Ruanyf weekly excerpt book job finished: "
        f"status={result.status} books={list(result.updated_book_ids)} "
        f"added={list(result.added_issue_numbers)} "
        f"dated={list(result.dated_issue_numbers)}"
    )
    return result


def enqueue_ruanyf_weekly_excerpt_book_job() -> str:
    run, _created = submit_local_job_once(
        job_type="library.ruanyf-weekly-excerpt-book",
        payload={},
    )
    return run.id


def _collect_excerpts(session: Session, user_id: int) -> dict[int, _Excerpt]:
    notes = session.exec(
        select(NoteNode)
        .where(NoteNode.user_id == int(user_id))
        .where(NoteNode.deleted_at.is_(None))
        .order_by(NoteNode.updated_at)
    ).all()
    excerpts: dict[int, _Excerpt] = {}
    for note in notes:
        match = NOTE_TITLE_RE.fullmatch(str(note.title or "").strip())
        if match is None or not _has_excerpt_content(note.content):
            continue
        issue_number = int(match.group("number"))
        excerpts[issue_number] = _Excerpt(
            issue_number=issue_number,
            title=match.group("title").strip(),
            content_html=note.content,
            note_id=str(note.id),
            source_url=_issue_source_url(note.content, issue_number),
            excerpt_date=_note_date(note),
        )
    return excerpts


def _has_excerpt_content(content_html: str) -> bool:
    soup = BeautifulSoup(content_html or "", "html.parser")
    text_without_urls = URL_RE.sub("", soup.get_text(" ", strip=True))
    compact_text = re.sub(r"\s+", "", text_without_urls)
    return len(compact_text) >= 8 or soup.find("img") is not None


def _issue_source_url(content_html: str, issue_number: int) -> str:
    match = ISSUE_SOURCE_RE.search(content_html or "")
    if match is not None and int(match.group("number")) == int(issue_number):
        return match.group(0)
    return f"https://github.com/ruanyf/weekly/blob/master/docs/issue-{int(issue_number)}.md"


def _note_date(note: NoteNode) -> str:
    timestamp = float(note.start_at or note.created_at or 0)
    return datetime.fromtimestamp(timestamp, DISPLAY_TIMEZONE).date().isoformat()


def _update_asset_from_excerpts(
    session: Session,
    asset: LibraryBookAsset,
    document: LinuxDoBookDocument,
    excerpts: dict[int, _Excerpt],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    existing_items: dict[int, LinuxDoTocItem] = {}
    for item in document.toc:
        match = TOC_ISSUE_RE.fullmatch(item.title)
        if match is not None:
            existing_items[int(match.group("number"))] = item
    missing = sorted(set(excerpts).difference(existing_items))
    dated: list[int] = []
    content_html = document.content_html
    content_markdown = document.content_markdown
    soup = BeautifulSoup(content_html, "html.parser")
    for issue_number, item in existing_items.items():
        excerpt = excerpts.get(issue_number)
        if excerpt is None:
            continue
        desired_title = _chapter_title(excerpt)
        if item.title == desired_title:
            continue
        old_title = item.title
        item.title = desired_title
        heading = soup.find(id=item.anchor)
        if isinstance(heading, Tag):
            heading.clear()
            heading.string = desired_title
        content_markdown = re.sub(
            rf"(?m)^#\s+{re.escape(old_title)}\s*$",
            f"# {desired_title}",
            content_markdown,
            count=1,
        )
        dated.append(issue_number)
    if dated:
        content_html = str(soup)
    if not missing and not dated:
        return (), ()

    chapters = {number: _build_chapter(excerpts[number]) for number in missing}
    all_items = {
        **existing_items,
        **{number: chapter.toc_item for number, chapter in chapters.items()},
    }
    ordered_numbers = sorted(all_items)
    document.toc = [all_items[number] for number in ordered_numbers]
    document.content_html = _merge_article_html(
        content_html,
        existing_items,
        chapters,
        ordered_numbers,
    )
    new_markdown = "\n\n".join(chapters[number].markdown for number in missing)
    document.content_markdown = content_markdown
    if new_markdown:
        document.content_markdown = (
            f"{content_markdown.rstrip()}\n\n{new_markdown}\n"
            if content_markdown.strip()
            else f"{new_markdown}\n"
        )
    first_issue, last_issue = ordered_numbers[0], ordered_numbers[-1]
    title = f"我的科技周刊摘抄 {first_issue}至{last_issue} 期"
    now = time.time()
    revision = hashlib.sha256(document.content_html.encode("utf-8")).hexdigest()
    document.title = title
    document.revision = revision
    document.post_count = len(document.toc)
    document.estimated_page_count = dynamic_book_html_page_count(document.content_html)

    metadata = dict(asset.metadata_json or {})
    source_notes = dict(metadata.get("ruanyf_excerpt_source_notes") or {})
    source_notes.update({str(number): excerpts[number].note_id for number in missing})
    excerpt_dates = dict(metadata.get("ruanyf_excerpt_dates") or {})
    excerpt_dates.update(
        {
            str(number): excerpts[number].excerpt_date
            for number in set(missing).union(dated)
        }
    )
    metadata.update(
        {
            "dynamic": True,
            "ruanyf_excerpt_auto_update": True,
            "ruanyf_excerpt_source_notes": source_notes,
            "ruanyf_excerpt_dates": excerpt_dates,
            "start_issue": first_issue,
            "latest_issue": last_issue,
            "revision": revision,
            "toc_count": len(document.toc),
            "post_count": len(document.toc),
            "estimated_page_count": document.estimated_page_count,
            "updated_at": now,
        }
    )
    asset.title = title
    asset.metadata_json = metadata
    asset.updated_at = now
    session.add(asset)
    _write_document(asset.owner_user_id, document)
    return tuple(missing), tuple(dated)


def _build_chapter(excerpt: _Excerpt) -> _Chapter:
    anchor = f"issue-{excerpt.issue_number}"
    title = _chapter_title(excerpt)
    article_html = (
        f'<article data-article-id="{anchor}">'
        f'<h1 id="{anchor}">{html.escape(title)}</h1>'
        f"{excerpt.content_html}</article>"
    )
    text = BeautifulSoup(excerpt.content_html, "html.parser").get_text("\n", strip=True)
    markdown = f"# {title}\n\n[原文链接]({excerpt.source_url})\n\n{text}"
    return _Chapter(
        issue_number=excerpt.issue_number,
        html=article_html,
        markdown=markdown,
        toc_item=LinuxDoTocItem(
            title=title,
            number="",
            level=1,
            anchor=anchor,
            source_post_number=excerpt.issue_number,
        ),
    )


def _chapter_title(excerpt: _Excerpt) -> str:
    return f"{excerpt.issue_number} {excerpt.title}（{excerpt.excerpt_date}）"


def _merge_article_html(
    content_html: str,
    existing_items: dict[int, LinuxDoTocItem],
    chapters: dict[int, _Chapter],
    ordered_numbers: list[int],
) -> str:
    soup = BeautifulSoup(content_html, "html.parser")
    existing_articles: dict[int, str] = {}
    for issue_number, item in existing_items.items():
        article = soup.find("article", attrs={"data-article-id": item.anchor})
        if not isinstance(article, Tag):
            heading = soup.find(id=item.anchor)
            article = heading.find_parent("article") if isinstance(heading, Tag) else None
        if isinstance(article, Tag):
            existing_articles[issue_number] = str(article)

    if len(existing_articles) != len(existing_items):
        return content_html + "".join(chapters[number].html for number in sorted(chapters))
    return "".join(
        chapters[number].html if number in chapters else existing_articles[number]
        for number in ordered_numbers
    )


def _storage_path(owner_user_id: int, topic_id: int) -> Path:
    return (
        get_settings().data_dir
        / "library-books"
        / f"user_{int(owner_user_id)}"
        / "linux-do"
        / str(int(topic_id))
        / "book.json"
    )


def _read_document(asset: LibraryBookAsset) -> LinuxDoBookDocument | None:
    topic_id = int((asset.metadata_json or {}).get("topic_id") or 0)
    try:
        payload = json.loads(
            _storage_path(asset.owner_user_id, topic_id).read_text(encoding="utf-8")
        )
        payload["toc"] = [LinuxDoTocItem(**item) for item in payload.get("toc") or []]
        return LinuxDoBookDocument(**payload)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _write_document(owner_user_id: int, document: LinuxDoBookDocument) -> None:
    path = _storage_path(owner_user_id, document.topic_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document.to_dict(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)
