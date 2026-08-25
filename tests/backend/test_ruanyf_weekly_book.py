from datetime import datetime, timezone
import json
from types import SimpleNamespace

from sqlmodel import select

from backend.core.library import ruanyf_weekly_book as weekly_book
from backend.core.notes import weekly_scheduler as weekly
from backend.core.jobs.scheduler import BACKGROUND_TASK_SPECS
from backend.models import LibraryBookAsset, LibraryBookPlacement, NoteNode


def _markdown(issue_number: int, title: str) -> str:
    return (
        f"# 科技爱好者周刊（第 {issue_number} 期）：{title}\n\n"
        f"这是第 {issue_number} 期正文。\n\n"
        "## 工具\n\n"
        f"- [相对链接](../docs/issue-{issue_number}.md)\n"
    )


def test_dynamic_weekly_book_creates_then_incrementally_updates_same_asset(
    session,
    auth_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        weekly_book,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    weekly.create_ruanyf_weekly_note(
        session,
        user_id=auth_user.id,
        issue=weekly.RuanyfWeeklyIssue(400, "旧笔记", "docs/issue-400.md"),
        publication=weekly.RuanyfWeeklyPublication(
            datetime(2026, 6, 19, tzinfo=timezone.utc),
            "old-note",
        ),
        template_note=None,
    )
    original_note_ids = {
        note.id for note in session.exec(select(NoteNode)).all()
    }
    sources = {
        401: _markdown(401, "如何赚到10亿美元"),
        402: _markdown(402, "我在智念 AI 的日子（小说）"),
        403: _markdown(403, "为什么 Dropbox 不成功"),
    }
    fetch_calls: list[int] = []

    def fetch_markdown(issue_number: int) -> str:
        fetch_calls.append(issue_number)
        return sources[issue_number]

    def fetch_publication_date(issue_number: int) -> str:
        return {
            401: "2026-06-26",
            402: "2026-07-03",
            403: "2026-07-10",
        }[issue_number]

    created = weekly_book.update_ruanyf_weekly_book(
        session,
        latest_issue=weekly.RuanyfWeeklyIssue(402, "最新", "docs/issue-402.md"),
        fetch_markdown=fetch_markdown,
        fetch_publication_date=fetch_publication_date,
    )

    assert created.status == "created"
    assert created.added_issue_numbers == (401, 402)
    assert created.book_id == f"ruanyf-weekly:{auth_user.id}:401"
    asset = session.get(LibraryBookAsset, created.book_id)
    assert asset is not None
    assert asset.title == "科技爱好者周刊 401至402 期"
    assert asset.metadata_json["dynamic"] is True
    placement = session.exec(
        select(LibraryBookPlacement).where(
            LibraryBookPlacement.book_asset_id == created.book_id
        )
    ).one()
    original_placement_id = placement.id
    storage_path = weekly_book._storage_path(
        auth_user.id,
        int(asset.metadata_json["topic_id"]),
    )
    document = json.loads(storage_path.read_text(encoding="utf-8"))
    assert [item["title"] for item in document["toc"]] == [
        "401 如何赚到10亿美元（2026-06-26）",
        "402 我在智念 AI 的日子（小说）（2026-07-03）",
    ]
    assert 'data-article-id="issue-401"' in document["content_html"]
    assert {note.id for note in session.exec(select(NoteNode)).all()} == original_note_ids

    fetch_calls.clear()
    updated = weekly_book.update_ruanyf_weekly_book(
        session,
        latest_issue=weekly.RuanyfWeeklyIssue(403, "最新", "docs/issue-403.md"),
        fetch_markdown=fetch_markdown,
        fetch_publication_date=fetch_publication_date,
    )

    assert updated.status == "updated"
    assert updated.book_id == created.book_id
    assert updated.added_issue_numbers == (403,)
    assert fetch_calls == [403]
    session.refresh(asset)
    assert asset.title == "科技爱好者周刊 401至403 期"
    assert asset.metadata_json["latest_issue"] == 403
    assert session.get(LibraryBookPlacement, original_placement_id) is not None
    assert {note.id for note in session.exec(select(NoteNode)).all()} == original_note_ids

    fetch_calls.clear()
    unchanged = weekly_book.update_ruanyf_weekly_book(
        session,
        latest_issue=weekly.RuanyfWeeklyIssue(403, "最新", "docs/issue-403.md"),
        fetch_markdown=fetch_markdown,
        fetch_publication_date=fetch_publication_date,
    )
    assert unchanged.status == "up_to_date"
    assert unchanged.book_id == created.book_id
    assert fetch_calls == []


def test_backfill_weekly_publication_dates_updates_toc_and_headings(
    session,
    auth_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        weekly_book,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    asset = LibraryBookAsset(
        id="ebook:weekly:301-400",
        resource_type="linux-do-book",
        owner_user_id=auth_user.id,
        source_kind="ebook:test",
        title="科技爱好者周刊 301至400 期",
        metadata_json={"topic_id": -301400},
    )
    session.add(asset)
    session.commit()
    document = weekly_book.LinuxDoBookDocument(
        topic_id=-301400,
        title=asset.title,
        author="阮一峰",
        source_url="https://github.com/ruanyf/weekly",
        content_html=(
            '<article data-article-id="article-1">'
            '<h1 id="article-1">400 rsync 的争论</h1><p>正文</p></article>'
        ),
        content_markdown="# 400 rsync 的争论\n\n正文\n",
        toc=[
            weekly_book.LinuxDoTocItem(
                title="400 rsync 的争论",
                number="",
                level=1,
                anchor="article-1",
            )
        ],
        revision="old",
        post_count=1,
        selected_reply_count=0,
        imported_at=1.0,
    )
    weekly_book._write_document(auth_user.id, document)

    changed = weekly_book.backfill_ruanyf_weekly_publication_dates(
        session,
        {400: "2026-06-12"},
    )

    assert changed == {asset.id: (400,)}
    updated = weekly_book._read_document(asset)
    assert updated is not None
    assert updated.toc[0].title == "400 rsync 的争论（2026-06-12）"
    assert '<h1 id="article-1">400 rsync 的争论（2026-06-12）</h1>' in updated.content_html
    assert "# 400 rsync 的争论（2026-06-12）" in updated.content_markdown
    session.refresh(asset)
    assert asset.metadata_json["issue_publication_dates"]["400"] == "2026-06-12"


def test_registered_weekly_task_updates_star_note():
    spec = next(item for item in BACKGROUND_TASK_SPECS if item.key == weekly.RUANYF_WEEKLY_TASK_NAME)

    assert spec.title == "阮一峰周刊笔记"
    assert spec.category == "笔记"
    assert spec.action.__name__ == "_enqueue_ruanyf_weekly_note"
    assert "写入星图笔记" in spec.description


def test_weekly_book_queue_does_not_share_star_note_dedup_key(monkeypatch):
    calls = []
    monkeypatch.setattr(
        weekly,
        "submit_local_job_once",
        lambda **kwargs: (calls.append(kwargs) or SimpleNamespace(id="local-1"), True),
    )

    assert weekly.enqueue_ruanyf_weekly_book_job() == "local-1"
    assert calls == [{"job_type": "library.ruanyf-weekly-book", "payload": {}}]
    assert weekly.RUANYF_WEEKLY_BOOK_TASK_NAME != weekly.RUANYF_WEEKLY_TASK_NAME


def test_weekly_book_retries_until_friday_publication_arrives():
    assert weekly._ruanyf_weekly_book_should_retry("up_to_date") is True
    assert weekly._ruanyf_weekly_book_should_retry("source_unavailable") is True
    assert weekly._ruanyf_weekly_book_should_retry("updated") is False
