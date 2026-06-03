from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from backend.api.notes import _try_execute_note_program_sql_scan
from backend.models import NoteEdge, NoteNode, User
from backend.schemas import (
    NoteProgramExecutor,
    NoteProgramMatcher,
    NoteProgramRequest,
    NoteProgramRule,
)


def _session_with_graph():
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
            NoteNode(id="n1", numeric_id=101, user_id=1, title="a", start_at=10),
            NoteNode(id="n2", numeric_id=102, user_id=1, title="b", start_at=20),
            NoteNode(id="n3", numeric_id=103, user_id=1, title="c", start_at=30),
        ]
    )
    session.add_all(
        [
            NoteEdge(id="e12", user_id=1, source_id="101", target_id="102", label=None, created_at=1),
            NoteEdge(id="e23", user_id=1, source_id="102", target_id="103", label=None, created_at=1),
            NoteEdge(id="e31", user_id=1, source_id="103", target_id="101", label=None, created_at=1),
        ]
    )
    session.commit()
    return session, user


def _scan_request(*, include_edges: bool, limit: int = 100) -> NoteProgramRequest:
    request = NoteProgramRequest(executor=NoteProgramExecutor(kind="scan"))
    request.program.select.default = False
    request.program.select.rules = [
        NoteProgramRule(
            action="include",
            matcher=NoteProgramMatcher(kind="field", field="start_at", op="between", values=[0, 100]),
        )
    ]
    request.result.include_edges = include_edges
    request.result.order_by = "start_at"
    request.result.order_desc = False
    request.result.limit = limit
    return request


def _scan_all_request(*, include_edges: bool, limit: int = 100) -> NoteProgramRequest:
    request = NoteProgramRequest(executor=NoteProgramExecutor(kind="scan"))
    request.program.select.default = False
    request.program.select.rules = [
        NoteProgramRule(action="include", matcher=NoteProgramMatcher(kind="all"))
    ]
    request.result.include_edges = include_edges
    request.result.order_by = "start_at"
    request.result.order_desc = False
    request.result.limit = limit
    return request


def test_sql_scan_with_edges_only_returns_edges_between_visible_nodes():
    session, user = _session_with_graph()
    try:
        result = _try_execute_note_program_sql_scan(
            _scan_request(include_edges=True, limit=2),
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is not None
        assert result["total_nodes"] == 3
        assert [node["id"] for node in result["nodes"]] == [101, 102]
        assert [edge["id"] for edge in result["edges"]] == ["e12"]
        assert result["edges"][0]["source_id"] == 101
        assert result["edges"][0]["target_id"] == 102
    finally:
        session.close()


def test_sql_scan_without_edges_keeps_edges_empty():
    session, user = _session_with_graph()
    try:
        result = _try_execute_note_program_sql_scan(
            _scan_request(include_edges=False, limit=2),
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is not None
        assert result["total_nodes"] == 3
        assert len(result["nodes"]) == 2
        assert result["edges"] == []
        assert result["total_edges"] == 0
    finally:
        session.close()


def test_sql_scan_include_all_uses_fast_path_with_visible_edges():
    session, user = _session_with_graph()
    try:
        result = _try_execute_note_program_sql_scan(
            _scan_all_request(include_edges=True, limit=2),
            current_user=user,
            user_id=user.id,
            session=session,
        )

        assert result is not None
        assert result["total_nodes"] == 3
        assert [node["id"] for node in result["nodes"]] == [101, 102]
        assert [edge["id"] for edge in result["edges"]] == ["e12"]
    finally:
        session.close()
