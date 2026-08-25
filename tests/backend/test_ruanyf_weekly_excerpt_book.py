import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlmodel import select

from backend.core.jobs import scheduler
from backend.core.library import ruanyf_weekly_excerpt_book as excerpt_book
from backend.core.library.linux_do_book import LinuxDoBookDocument, LinuxDoTocItem
from backend.models import LibraryBookAsset, NoteNode


def _article(issue_number: int) -> str:
    anchor = f"article-{issue_number}"
    return (
        f'<article data-article-id="{anchor}">'
        f'<h1 id="{anchor}">{issue_number} 第 {issue_number} 期</h1>'
        f"<p>第 {issue_number} 期旧正文</p></article>"
    )


def _create_book(session, user_id: int) -> LibraryBookAsset:
    asset = LibraryBookAsset(
        id="ebook:weekly-excerpts",
        resource_type="rich-text",
        owner_user_id=user_id,
        source_kind="ebook:weekly-excerpts",
        title="我的科技周刊摘抄 402至404 期",
        author="测试用户",
        metadata_json={"topic_id": -402404, "format": "epub"},
    )
    session.add(asset)
    session.commit()
    document = LinuxDoBookDocument(
        topic_id=-402404,
        title=asset.title,
        author=asset.author,
        source_url="",
        content_html=_article(402) + _article(404),
        content_markdown="# 402 第 402 期\n\n旧正文\n\n# 404 第 404 期\n\n旧正文\n",
        toc=[
            LinuxDoTocItem(
                title=f"{number} 第 {number} 期",
                number="",
                level=1,
                anchor=f"article-{number}",
            )
            for number in (402, 404)
        ],
        revision="old",
        post_count=2,
        selected_reply_count=0,
        imported_at=1.0,
    )
    excerpt_book._write_document(user_id, document)
    return asset


def test_excerpt_book_backfills_missing_and_late_completed_issues(
    session,
    auth_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        excerpt_book,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    asset = _create_book(session, auth_user.id)
    session.add_all(
        [
            NoteNode(
                id="weekly-excerpt-402",
                numeric_id=9402,
                user_id=auth_user.id,
                title="科技周刊第402期：我在智念 AI 的日子（小说）",
                content="<p>第 402 期已有摘抄。</p>",
                start_at=datetime(2026, 7, 3, 12, tzinfo=timezone.utc).timestamp(),
            ),
            NoteNode(
                id="weekly-excerpt-403",
                numeric_id=9403,
                user_id=auth_user.id,
                title="科技周刊第第第403期：为什么 Dropbox 不成功",
                content="<p>这是后来补写的第 403 期摘抄，应该插回正确位置。</p>",
                start_at=datetime(2026, 7, 10, 12, tzinfo=timezone.utc).timestamp(),
            ),
            NoteNode(
                id="weekly-excerpt-404",
                numeric_id=9404,
                user_id=auth_user.id,
                title="科技周刊第404期：你需要知道的 AI 内存知识",
                content="<p>第 404 期已有摘抄。</p>",
                start_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc).timestamp(),
            ),
            NoteNode(
                id="weekly-excerpt-405",
                numeric_id=9405,
                user_id=auth_user.id,
                title="科技周刊第第第405期：资源，社会公平与算力",
                content=(
                    '<p><a href="https://github.com/ruanyf/weekly/blob/master/docs/issue-405.md">'
                    "https://github.com/ruanyf/weekly/blob/master/docs/issue-405.md</a></p>"
                    "<p>这是第 405 期已经完成的个人摘抄正文。</p>"
                ),
                start_at=datetime(2026, 7, 24, 12, tzinfo=timezone.utc).timestamp(),
            ),
            NoteNode(
                id="weekly-excerpt-406",
                numeric_id=9406,
                user_id=auth_user.id,
                title="科技周刊第406期：尚未摘抄",
                content=(
                    '<p><a href="https://github.com/ruanyf/weekly/blob/master/docs/issue-406.md">'
                    "https://github.com/ruanyf/weekly/blob/master/docs/issue-406.md</a></p>"
                ),
                start_at=datetime(2026, 7, 31, 12, tzinfo=timezone.utc).timestamp(),
            ),
        ]
    )
    session.commit()

    first = excerpt_book.update_ruanyf_weekly_excerpt_books(
        session,
        owner_user_id=auth_user.id,
    )

    assert first.status == "updated"
    assert first.added_issue_numbers == (403, 405)
    assert first.dated_issue_numbers == (402, 404)
    session.refresh(asset)
    assert asset.title == "我的科技周刊摘抄 402至405 期"
    assert asset.metadata_json["ruanyf_excerpt_auto_update"] is True
    document = excerpt_book._read_document(asset)
    assert document is not None
    assert [item.title for item in document.toc] == [
        "402 我在智念 AI 的日子（小说）（2026-07-03）",
        "403 为什么 Dropbox 不成功（2026-07-10）",
        "404 你需要知道的 AI 内存知识（2026-07-17）",
        "405 资源，社会公平与算力（2026-07-24）",
    ]
    assert 'data-article-id="issue-403"' in document.content_html
    assert 'data-article-id="issue-405"' in document.content_html
    assert "issue-406" not in document.content_html

    unchanged = excerpt_book.update_ruanyf_weekly_excerpt_books(
        session,
        owner_user_id=auth_user.id,
    )
    assert unchanged.status == "up_to_date"

    delayed = session.exec(
        select(NoteNode).where(NoteNode.title == "科技周刊第406期：尚未摘抄")
    ).one()
    delayed.content += "<p>延期一周后补上的正文，现在应该可以入书。</p>"
    session.add(delayed)
    session.commit()

    second = excerpt_book.update_ruanyf_weekly_excerpt_books(
        session,
        owner_user_id=auth_user.id,
    )
    assert second.status == "updated"
    assert second.added_issue_numbers == (406,)
    assert second.dated_issue_numbers == ()
    session.refresh(asset)
    assert asset.title == "我的科技周刊摘抄 402至406 期"


def test_excerpt_book_task_is_available_with_sunday_midnight_schedule():
    spec = scheduler.get_background_task_spec(
        excerpt_book.RUANYF_WEEKLY_EXCERPT_BOOK_TASK_KEY
    )
    assert spec is not None
    assert spec.title == "科技周刊摘抄入书"
    assert spec.default_visible is False
    assert "延期补写" in spec.description
    policy = scheduler._default_background_task_schedule_policy(spec.key)
    assert policy is not None
    assert policy["trigger"] == {
        "type": "weekly",
        "weekdays": [7],
        "time": "00:00",
    }


def test_excerpt_book_document_is_json_serializable(
    session,
    auth_user,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        excerpt_book,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    asset = _create_book(session, auth_user.id)
    payload = json.loads(
        excerpt_book._storage_path(auth_user.id, -402404).read_text(encoding="utf-8")
    )
    assert payload["title"] == asset.title
