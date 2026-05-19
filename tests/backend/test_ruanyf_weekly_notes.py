from datetime import datetime, timezone

from sqlmodel import select

from backend.core import weekly_note_scheduler as weekly
from backend.models import NoteNode


def make_weekly_note(auth_user, note_id: str, issue_number: int, title: str, *, custom_fields=None):
    return NoteNode(
        id=note_id,
        user_id=auth_user.id,
        title=title,
        content="",
        weight=0,
        node_type="note",
        note_types=[{"key": "note", "weight": 100}],
        note_categories=[{"key": "general", "weight": 100}],
        primary_category="general",
        note_form="note",
        note_kind="note",
        note_scene="note",
        node_status="idea",
        lifecycle_stage="idea",
        private_level=0,
        custom_fields=custom_fields or [],
        created_at=float(issue_number),
        updated_at=float(issue_number),
        start_at=float(issue_number),
        history=[],
    )


def test_parse_ruanyf_weekly_readme_uses_highest_issue():
    issue = weekly.parse_ruanyf_weekly_readme(
        """
        # 科技爱好者周刊

        - 第 394 期：[第二次 API 开放浪潮](docs/issue-394.md)
        - 第 393 期：[脑腐状态](docs/issue-393.md)
        """
    )

    assert issue == weekly.RuanyfWeeklyIssue(
        number=394,
        title="第二次 API 开放浪潮",
        path="docs/issue-394.md",
    )
    assert issue.source_url == "https://github.com/ruanyf/weekly/blob/master/docs/issue-394.md"


def test_build_note_title_preserves_latest_existing_prefix_style():
    title = weekly.build_ruanyf_weekly_note_title(
        395,
        "新的标题",
        "科技周刊第第第394期：第二次 API 开放浪潮",
    )

    assert title == "科技周刊第第第395期：新的标题"


def test_parse_publication_prefers_release_commit():
    publication = weekly.parse_ruanyf_weekly_publication(
        393,
        [
            {
                "sha": "fix-sha",
                "commit": {
                    "message": "docs(issue-393): fix wrong link",
                    "author": {"date": "2026-04-17T04:48:27Z"},
                },
            },
            {
                "sha": "release-sha",
                "commit": {
                    "message": "docs: release issue 393",
                    "author": {"date": "2026-04-16T23:24:34Z"},
                },
            },
        ],
    )

    assert publication == weekly.RuanyfWeeklyPublication(
        published_at=datetime(2026, 4, 16, 23, 24, 34, tzinfo=timezone.utc),
        commit_sha="release-sha",
    )


def test_maybe_create_weekly_note_creates_note_after_current_friday_publication(
    session,
    auth_user,
    monkeypatch,
):
    session.add(
        make_weekly_note(
            auth_user,
            "issue-394",
            394,
            "科技周刊第第第394期：第二次 API 开放浪潮",
        )
    )
    session.commit()
    latest_issue = weekly.RuanyfWeeklyIssue(
        number=395,
        title="下一期标题",
        path="docs/issue-395.md",
    )
    publication = weekly.RuanyfWeeklyPublication(
        published_at=datetime(2026, 5, 8, 3, 30, tzinfo=timezone.utc),
        commit_sha="abc123",
    )
    monkeypatch.setattr(weekly, "fetch_latest_ruanyf_weekly_issue", lambda: latest_issue)
    monkeypatch.setattr(weekly, "fetch_ruanyf_weekly_publication", lambda issue: publication)

    result = weekly.maybe_create_ruanyf_weekly_note(
        session,
        now=datetime(2026, 5, 8, 12, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert result.status == "created"
    note = session.exec(select(NoteNode).where(NoteNode.id == result.created_note_id)).one()
    assert note.numeric_id == int(result.created_note_id)
    assert note.legacy_id and note.legacy_id != note.id
    assert note.user_id == auth_user.id
    assert note.title == "科技周刊第第第395期：下一期标题"
    assert note.content == (
        '<p><a href="https://github.com/ruanyf/weekly/blob/master/docs/issue-395.md" '
        'target="_blank" rel="noopener noreferrer">'
        "https://github.com/ruanyf/weekly/blob/master/docs/issue-395.md</a></p>"
    )
    assert note.start_at == publication.published_at.timestamp()
    assert [weekly.RUANYF_WEEKLY_ISSUE_FIELD, "number", 395] in note.custom_fields
    assert [weekly.RUANYF_WEEKLY_COMMIT_SHA_FIELD, "string", "abc123"] in note.custom_fields


def test_maybe_create_weekly_note_skips_existing_hidden_issue(
    session,
    auth_user,
    monkeypatch,
):
    session.add(
        make_weekly_note(
            auth_user,
            "issue-394",
            394,
            "科技周刊第第第394期：第二次 API 开放浪潮",
        )
    )
    session.add(
        make_weekly_note(
            auth_user,
            "hidden-395",
            0,
            "临时标题",
            custom_fields=[[weekly.RUANYF_WEEKLY_ISSUE_FIELD, "number", 395]],
        )
    )
    session.commit()
    latest_issue = weekly.RuanyfWeeklyIssue(395, "下一期标题", "docs/issue-395.md")
    publication = weekly.RuanyfWeeklyPublication(
        datetime(2026, 5, 8, 3, 30, tzinfo=timezone.utc),
        "abc123",
    )
    monkeypatch.setattr(weekly, "fetch_latest_ruanyf_weekly_issue", lambda: latest_issue)
    monkeypatch.setattr(weekly, "fetch_ruanyf_weekly_publication", lambda issue: publication)

    result = weekly.maybe_create_ruanyf_weekly_note(
        session,
        now=datetime(2026, 5, 8, 12, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert result.status == "already_exists"
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == auth_user.id)).all()
    assert len(notes) == 2


def test_maybe_create_weekly_note_skips_publication_outside_current_friday(
    session,
    auth_user,
    monkeypatch,
):
    session.add(
        make_weekly_note(
            auth_user,
            "issue-394",
            394,
            "科技周刊第第第394期：第二次 API 开放浪潮",
        )
    )
    session.commit()
    monkeypatch.setattr(
        weekly,
        "fetch_latest_ruanyf_weekly_issue",
        lambda: weekly.RuanyfWeeklyIssue(395, "旧发布", "docs/issue-395.md"),
    )
    monkeypatch.setattr(
        weekly,
        "fetch_ruanyf_weekly_publication",
        lambda issue: weekly.RuanyfWeeklyPublication(
            datetime(2026, 5, 1, 3, 30, tzinfo=timezone.utc),
            "abc123",
        ),
    )

    result = weekly.maybe_create_ruanyf_weekly_note(
        session,
        now=datetime(2026, 5, 8, 12, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert result.status == "published_outside_window"
    notes = session.exec(select(NoteNode).where(NoteNode.user_id == auth_user.id)).all()
    assert len(notes) == 1


def test_maybe_create_weekly_note_skips_after_current_window_success(
    session,
    auth_user,
    monkeypatch,
):
    source_url = "https://github.com/ruanyf/weekly/blob/master/docs/issue-395.md"
    note = make_weekly_note(
        auth_user,
        "issue-395",
        395,
        "科技周刊第第第395期：下一期标题",
        custom_fields=[
            [weekly.RUANYF_WEEKLY_ISSUE_FIELD, "number", 395],
            [weekly.RUANYF_WEEKLY_SOURCE_URL_FIELD, "string", source_url],
            [weekly.RUANYF_WEEKLY_PUBLISHED_AT_FIELD, "string", "2026-05-15T03:30:00Z"],
        ],
    )
    note.content = source_url
    note.start_at = datetime(2026, 5, 15, 3, 30, tzinfo=timezone.utc).timestamp()
    session.add(note)
    session.commit()

    def fail_fetch():
        raise AssertionError("current window success should skip remote fetch")

    monkeypatch.setattr(weekly, "fetch_latest_ruanyf_weekly_issue", fail_fetch)

    result = weekly.maybe_create_ruanyf_weekly_note(
        session,
        now=datetime(2026, 5, 15, 22, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert result.status == "already_completed_window"


def test_enqueue_weekly_note_job_skips_completed_current_window(monkeypatch):
    class DummySession:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(weekly, "Session", lambda _engine: DummySession())
    monkeypatch.setattr(
        weekly,
        "ruanyf_weekly_note_completed_for_current_window",
        lambda session, *, now=None: True,
    )

    def fail_enqueue(*args, **kwargs):
        raise AssertionError("completed weekly window should not enqueue a task")

    monkeypatch.setattr(weekly.background_task_queue, "enqueue", fail_enqueue)

    queue_task_id = weekly.enqueue_ruanyf_weekly_note_job(
        now=datetime(2026, 5, 15, 22, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert queue_task_id is None


def test_maybe_create_weekly_note_skips_without_existing_target_user(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        weekly,
        "fetch_latest_ruanyf_weekly_issue",
        lambda: weekly.RuanyfWeeklyIssue(395, "下一期标题", "docs/issue-395.md"),
    )

    result = weekly.maybe_create_ruanyf_weekly_note(
        session,
        now=datetime(2026, 5, 8, 12, 0, tzinfo=weekly.RUANYF_WEEKLY_TIMEZONE),
    )

    assert result.status == "no_target_user"
    assert session.exec(select(NoteNode)).all() == []
