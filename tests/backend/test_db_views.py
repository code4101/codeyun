from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, text

from backend.db_views import refresh_sqlite_readable_views
from backend.models import NoteNode


def test_refresh_sqlite_readable_views_creates_time_text_columns():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            NoteNode(
                id="note-1",
                user_id=1,
                title="Readable View",
                content="demo",
                created_at=1700000000.5,
                updated_at=1700000100.25,
                start_at=1700000200.75,
            )
        )
        session.commit()

    refresh_sqlite_readable_views(engine)

    with Session(engine) as session:
        row = session.exec(
            text(
                """
                SELECT id, created_at_local_text, updated_at_local_text, start_at_local_text
                FROM notenode_readable
                WHERE id = 'note-1'
                """
            )
        ).one()

    assert row[0] == "note-1"
    assert isinstance(row[1], str) and row[1]
    assert isinstance(row[2], str) and row[2]
    assert isinstance(row[3], str) and row[3]


def test_refresh_sqlite_readable_views_tracks_added_columns():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE demo (id INTEGER PRIMARY KEY, created_at FLOAT)")
        conn.exec_driver_sql("INSERT INTO demo (id, created_at) VALUES (1, 1700000000.0)")

    refresh_sqlite_readable_views(engine)

    with engine.begin() as conn:
        before = conn.exec_driver_sql("PRAGMA table_info(demo_readable)").fetchall()
        conn.exec_driver_sql("ALTER TABLE demo ADD COLUMN note TEXT")
        conn.exec_driver_sql("UPDATE demo SET note = 'hello' WHERE id = 1")

    refresh_sqlite_readable_views(engine)

    with engine.begin() as conn:
        after = conn.exec_driver_sql("PRAGMA table_info(demo_readable)").fetchall()
        row = conn.exec_driver_sql("SELECT id, note, created_at_local_text FROM demo_readable").fetchone()

    before_names = [item[1] for item in before]
    after_names = [item[1] for item in after]

    assert "note" not in before_names
    assert "note" in after_names
    assert row[1] == "hello"
    assert isinstance(row[2], str) and row[2]
