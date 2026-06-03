from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api.notes import _try_execute_note_calendar_summary_sql_scan
from backend.models import NoteNode, User
from backend.schemas import (
    NoteCalendarSummaryBucketRequest,
    NoteCalendarSummaryRequest,
    NoteProgramExecutor,
    NoteProgramMatcher,
    NoteProgramRequest,
    NoteProgramRule,
)


def _session_with_notes():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    user = User(id=1, username="alice", hashed_password="x")
    session.add(user)
    session.add_all(
        [
            NoteNode(id="n1", numeric_id=101, user_id=1, title="public a", start_at=10, private_level=0),
            NoteNode(id="n2", numeric_id=102, user_id=1, title="private", start_at=20, private_level=1),
            NoteNode(id="n3", numeric_id=103, user_id=1, title="public b", start_at=30, private_level=0),
        ]
    )
    session.commit()
    return session, user


def _summary_request(*tail_rules: NoteProgramRule) -> NoteCalendarSummaryRequest:
    request = NoteProgramRequest(executor=NoteProgramExecutor(kind="scan"))
    request.program.select.default = False
    request.program.select.rules = [
        NoteProgramRule(
            action="include",
            matcher=NoteProgramMatcher(kind="field", field="start_at", op="between", values=[0, 100]),
        ),
        *tail_rules,
    ]
    request.result.include_edges = False
    request.result.order_by = "start_at"
    request.result.order_desc = False
    request.result.limit = 100
    return NoteCalendarSummaryRequest(
        query=request,
        buckets=[NoteCalendarSummaryBucketRequest(key="all", start_at=0, end_at=100, mode="era", limit=10)],
    )


def test_calendar_summary_applies_private_level_filter():
    session, user = _session_with_notes()
    try:
        summary = _summary_request(
            NoteProgramRule(
                action="filter",
                matcher=NoteProgramMatcher(kind="field", field="private_level", op="lte", value=0),
            )
        )

        result = _try_execute_note_calendar_summary_sql_scan(
            summary,
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is not None
        assert result["total_nodes"] == 2
        assert result["buckets"][0]["total_nodes"] == 2
        assert {node["title"] for node in result["nodes"]} == {"public a", "public b"}
    finally:
        session.close()


def test_calendar_summary_rejects_non_filter_tail_rule():
    session, user = _session_with_notes()
    try:
        summary = _summary_request(
            NoteProgramRule(
                action="exclude",
                matcher=NoteProgramMatcher(kind="field", field="private_level", op="gte", value=1),
            )
        )

        result = _try_execute_note_calendar_summary_sql_scan(
            summary,
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is None
    finally:
        session.close()


def test_calendar_summary_applies_custom_field_exclude_rule():
    session, user = _session_with_notes()
    try:
        note = session.get(NoteNode, "n1")
        assert note is not None
        note.custom_fields = [["wechat_daily_source", "来源", "mf:v4_db_storage"]]
        session.add(note)
        session.commit()

        summary = _summary_request(
            NoteProgramRule(
                action="exclude",
                matcher=NoteProgramMatcher(
                    kind="field",
                    field="custom_fields.wechat_daily_source",
                    op="eq",
                    value="mf:v4_db_storage",
                ),
            )
        )

        result = _try_execute_note_calendar_summary_sql_scan(
            summary,
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is not None
        assert result["total_nodes"] == 2
        assert result["buckets"][0]["total_nodes"] == 2
        assert {node["title"] for node in result["nodes"]} == {"private", "public b"}
    finally:
        session.close()
