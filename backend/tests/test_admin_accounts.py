from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from backend.api import admin as admin_api
from backend.core.auth import verify_password
from backend.models import User


def test_admin_accounts_lists_all_accounts_for_superuser():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(
            User(
                username="root",
                nickname="主账号",
                email="root@example.com",
                phone="13800000001",
                hashed_password="hashed",
                password_plain="root-secret",
                is_active=True,
                is_superuser=True,
                created_at=100.0,
                updated_at=100.0,
            )
        )
        session.add(
            User(
                username="alice",
                nickname="测试用户",
                email="alice@example.com",
                phone=None,
                hashed_password="hashed",
                password_plain="alice-plain",
                is_active=True,
                is_superuser=False,
                created_at=200.0,
                updated_at=200.0,
            )
        )
        session.add(
            User(
                username="bob",
                nickname="",
                email=None,
                phone=None,
                hashed_password="hashed",
                password_plain="未知",
                is_active=False,
                is_superuser=False,
                created_at=150.0,
                updated_at=150.0,
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(admin_api.router, prefix="/api/admin")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[admin_api.get_session] = override_get_session
    app.dependency_overrides[admin_api.get_current_active_superuser] = lambda: User(
        id=999,
        username="tester",
        nickname="超管",
        phone=None,
        hashed_password="hashed",
        password_plain="tester-secret",
        is_active=True,
        is_superuser=True,
        created_at=1.0,
        updated_at=1.0,
    )

    client = TestClient(app)

    response = client.get("/api/admin/accounts")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "username": "root",
            "nickname": "主账号",
            "email": "root@example.com",
            "phone": "13800000001",
            "password_plain": "root-secret",
            "is_active": True,
            "is_superuser": True,
            "created_at": 100.0,
        },
        {
            "id": 3,
            "username": "bob",
            "nickname": "",
            "email": None,
            "phone": None,
            "password_plain": "未知",
            "is_active": False,
            "is_superuser": False,
            "created_at": 150.0,
        },
        {
            "id": 2,
            "username": "alice",
            "nickname": "测试用户",
            "email": "alice@example.com",
            "phone": None,
            "password_plain": "alice-plain",
            "is_active": True,
            "is_superuser": False,
            "created_at": 200.0,
        },
    ]


def test_admin_can_create_account():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)

    app = FastAPI()
    app.include_router(admin_api.router, prefix="/api/admin")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[admin_api.get_session] = override_get_session
    app.dependency_overrides[admin_api.get_current_active_superuser] = lambda: User(
        id=999,
        username="tester",
        nickname="超管",
        phone=None,
        hashed_password="hashed",
        password_plain="tester-secret",
        is_active=True,
        is_superuser=True,
        created_at=1.0,
        updated_at=1.0,
    )

    client = TestClient(app)

    response = client.post(
        "/api/admin/accounts",
        json={
            "username": "new-admin",
            "password": "new-secret",
            "nickname": "新账号",
            "is_superuser": True,
            "email": "new-admin@example.com",
            "phone": "13612345678",
        },
    )

    assert response.status_code == 200
    assert response.json()["username"] == "new-admin"
    assert response.json()["nickname"] == "新账号"
    assert response.json()["is_superuser"] is True
    assert response.json()["email"] == "new-admin@example.com"
    assert response.json()["phone"] == "13612345678"
    assert response.json()["password_plain"] == "new-secret"
    assert response.json()["is_active"] is True

    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == "new-admin")).one()

    assert user.nickname == "新账号"
    assert user.is_superuser is True
    assert user.email == "new-admin@example.com"
    assert user.phone == "13612345678"
    assert user.password_plain == "new-secret"
    assert verify_password("new-secret", user.hashed_password)


def test_admin_can_reset_account_password_and_plaintext():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(
            User(
                username="alice",
                nickname="旧备注",
                email=None,
                phone=None,
                hashed_password="old-hash",
                password_plain="未知",
                is_active=True,
                is_superuser=False,
                created_at=200.0,
                updated_at=200.0,
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(admin_api.router, prefix="/api/admin")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[admin_api.get_session] = override_get_session
    app.dependency_overrides[admin_api.get_current_active_superuser] = lambda: User(
        id=999,
        username="tester",
        nickname="超管",
        phone=None,
        hashed_password="hashed",
        password_plain="tester-secret",
        is_active=True,
        is_superuser=True,
        created_at=1.0,
        updated_at=1.0,
    )

    client = TestClient(app)

    response = client.post(
        "/api/admin/accounts/1/password",
        json={"password": "new-secret"},
    )

    assert response.status_code == 200
    assert response.json()["password_plain"] == "new-secret"
    assert response.json()["email"] is None
    assert response.json()["phone"] is None

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).one()

    assert user.password_plain == "new-secret"
    assert verify_password("new-secret", user.hashed_password)


def test_admin_can_update_account_profile():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(
            User(
                username="alice",
                nickname="旧备注",
                email="old@example.com",
                phone=None,
                hashed_password="old-hash",
                password_plain="未知",
                is_active=True,
                is_superuser=False,
                created_at=200.0,
                updated_at=200.0,
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(admin_api.router, prefix="/api/admin")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[admin_api.get_session] = override_get_session
    app.dependency_overrides[admin_api.get_current_active_superuser] = lambda: User(
        id=999,
        username="tester",
        nickname="超管",
        phone=None,
        hashed_password="hashed",
        password_plain="tester-secret",
        is_active=True,
        is_superuser=True,
        created_at=1.0,
        updated_at=1.0,
    )

    client = TestClient(app)

    response = client.post(
        "/api/admin/accounts/1/profile",
        json={
            "nickname": "项目A测试号",
            "is_superuser": True,
            "password": "profile-secret",
            "email": "project-a@example.com",
            "phone": "13912345678",
        },
    )

    assert response.status_code == 200
    assert response.json()["nickname"] == "项目A测试号"
    assert response.json()["is_superuser"] is True
    assert response.json()["password_plain"] == "profile-secret"
    assert response.json()["email"] == "project-a@example.com"
    assert response.json()["phone"] == "13912345678"

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).one()

    assert user.nickname == "项目A测试号"
    assert user.is_superuser is True
    assert user.password_plain == "profile-secret"
    assert verify_password("profile-secret", user.hashed_password)
    assert user.email == "project-a@example.com"
    assert user.phone == "13912345678"


def test_admin_cannot_remove_last_superuser_role():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine, checkfirst=True)

    with Session(engine) as session:
        session.add(
            User(
                username="root",
                nickname="唯一超管",
                email="root@example.com",
                phone=None,
                hashed_password="hashed",
                password_plain="root-secret",
                is_active=True,
                is_superuser=True,
                created_at=100.0,
                updated_at=100.0,
            )
        )
        session.commit()

    app = FastAPI()
    app.include_router(admin_api.router, prefix="/api/admin")

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[admin_api.get_session] = override_get_session
    app.dependency_overrides[admin_api.get_current_active_superuser] = lambda: User(
        id=999,
        username="tester",
        nickname="超管",
        phone=None,
        hashed_password="hashed",
        password_plain="tester-secret",
        is_active=True,
        is_superuser=True,
        created_at=1.0,
        updated_at=1.0,
    )

    client = TestClient(app)

    response = client.post(
        "/api/admin/accounts/1/profile",
        json={
            "nickname": "唯一超管",
            "is_superuser": False,
            "email": "root@example.com",
            "phone": "",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "至少保留一个超级管理员账号"

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == 1)).one()

    assert user.is_superuser is True
