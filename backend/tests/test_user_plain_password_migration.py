from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, text

from backend.migrations import manager as migration_manager


def test_v25_add_user_plain_password_field_marks_legacy_users_unknown():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with Session(engine) as session:
        session.exec(
            text(
                """
                CREATE TABLE user (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL,
                    email VARCHAR,
                    hashed_password VARCHAR NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    is_superuser BOOLEAN NOT NULL DEFAULT 0,
                    created_at FLOAT NOT NULL,
                    updated_at FLOAT NOT NULL
                )
                """
            )
        )
        session.exec(
            text(
                """
                INSERT INTO user (
                    id, username, email, hashed_password, is_active, is_superuser, created_at, updated_at
                ) VALUES (
                    1, 'legacy', NULL, 'hashed', 1, 0, 100.0, 100.0
                )
                """
            )
        )
        session.commit()

    with Session(engine) as session:
        migration_manager.v25_add_user_plain_password_field(session)

    with Session(engine) as session:
        password_plain = session.exec(
            text("SELECT password_plain FROM user WHERE id = 1")
        ).one()[0]

    assert password_plain == "未知"


def test_v26_add_user_nickname_field_backfills_blank():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with Session(engine) as session:
        session.exec(
            text(
                """
                CREATE TABLE user (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL,
                    email VARCHAR,
                    hashed_password VARCHAR NOT NULL,
                    password_plain VARCHAR NOT NULL DEFAULT '未知',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    is_superuser BOOLEAN NOT NULL DEFAULT 0,
                    created_at FLOAT NOT NULL,
                    updated_at FLOAT NOT NULL
                )
                """
            )
        )
        session.exec(
            text(
                """
                INSERT INTO user (
                    id, username, email, hashed_password, password_plain, is_active, is_superuser, created_at, updated_at
                ) VALUES (
                    1, 'legacy', NULL, 'hashed', 'legacy-secret', 1, 0, 100.0, 100.0
                )
                """
            )
        )
        session.commit()

    with Session(engine) as session:
        migration_manager.v26_add_user_nickname_field(session)

    with Session(engine) as session:
        nickname = session.exec(
            text("SELECT nickname FROM user WHERE id = 1")
        ).one()[0]

    assert nickname == ""


def test_v27_add_user_phone_field_backfills_null():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with Session(engine) as session:
        session.exec(
            text(
                """
                CREATE TABLE user (
                    id INTEGER PRIMARY KEY,
                    username VARCHAR NOT NULL,
                    nickname VARCHAR NOT NULL DEFAULT '',
                    email VARCHAR,
                    hashed_password VARCHAR NOT NULL,
                    password_plain VARCHAR NOT NULL DEFAULT '未知',
                    is_active BOOLEAN NOT NULL DEFAULT 1,
                    is_superuser BOOLEAN NOT NULL DEFAULT 0,
                    created_at FLOAT NOT NULL,
                    updated_at FLOAT NOT NULL
                )
                """
            )
        )
        session.exec(
            text(
                """
                INSERT INTO user (
                    id, username, nickname, email, hashed_password, password_plain, is_active, is_superuser, created_at, updated_at
                ) VALUES (
                    1, 'legacy', '', NULL, 'hashed', 'legacy-secret', 1, 0, 100.0, 100.0
                )
                """
            )
        )
        session.commit()

    with Session(engine) as session:
        migration_manager.v27_add_user_phone_field(session)

    with Session(engine) as session:
        phone = session.exec(
            text("SELECT phone FROM user WHERE id = 1")
        ).one()[0]

    assert phone is None
