from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from backend.api import auth as auth_api
from backend.core.access.auth import get_password_hash
from backend.models import User


def test_login_json_backfills_plain_password_for_existing_user():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(
            User(
                username="alice",
                hashed_password=get_password_hash("alice-secret"),
                password_plain="未知",
                is_active=True,
                is_superuser=False,
                created_at=100.0,
                updated_at=100.0,
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/auth")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[auth_api.get_session] = override_get_session

    client = TestClient(app)

    response = client.post(
        "/api/auth/login/json",
        json={"username": "alice", "password": "alice-secret"},
    )

    assert response.status_code == 200

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "alice")).one()

    assert user.password_plain == "alice-secret"
