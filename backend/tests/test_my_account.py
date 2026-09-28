import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine
from backend.api.auth import router
from backend.core.access.auth import get_current_active_user, get_password_hash, verify_password
from backend.db import get_session
from backend.models import User


@pytest.fixture
def account(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "accounts.db"}', connect_args={'check_same_thread': False})
    User.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([User(id=1, username='self', hashed_password=get_password_hash('current-pass')),
                         User(id=2, username='other', nickname='untouched', hashed_password='unused')])
        session.commit()
    app = FastAPI()
    app.include_router(router, prefix='/auth')
    def sessions():
        with Session(engine) as session:
            yield session
    def current_user():
        with Session(engine) as session:
            return session.get(User, 1)
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_active_user] = current_user
    with TestClient(app) as client:
        yield client, engine, app
    engine.dispose()


def test_profile_updates_only_self_and_preserves_omitted_fields(account):
    client, engine, app = account
    result = client.patch('/auth/me', json={'nickname': ' 新昵称 ', 'phone': '+86 13800138000', 'email': 'me@example.com'})
    assert result.status_code == 200
    data = result.json()
    assert data['nickname'] == '新昵称'
    assert data['phone'] == '+86 13800138000'
    assert 'password_plain' not in data and 'hashed_password' not in data
    assert client.patch('/auth/me', json={'email': ''}).json()['email'] is None
    with Session(engine) as session:
        assert session.get(User, 1).nickname == '新昵称'
        assert session.get(User, 2).nickname == 'untouched'
    for payload in [{'id': 2}, {'is_superuser': True}, {'email': 'invalid'}, {'phone': 'abc'}]:
        assert client.patch('/auth/me', json=payload).status_code == 422
    app.dependency_overrides.pop(get_current_active_user)
    assert client.patch('/auth/me', json={'nickname': 'no'}).status_code == 401


def test_password_requires_current_password_and_changes_login(account):
    client, engine, app = account
    assert client.post('/auth/me/password', json={'current_password': 'wrong', 'new_password': 'changed-pass'}).status_code == 400
    with Session(engine) as session:
        assert verify_password('current-pass', session.get(User, 1).hashed_password)
    assert client.post('/auth/me/password', json={'current_password': 'current-pass', 'new_password': 'short'}).status_code == 422
    assert client.post('/auth/me/password', json={'current_password': 'current-pass', 'new_password': '字' * 25}).status_code == 422
    response = client.post('/auth/me/password', json={'current_password': 'current-pass', 'new_password': 'changed-pass'})
    assert response.status_code == 204 and not response.content
    with Session(engine) as session:
        assert verify_password('changed-pass', session.get(User, 1).hashed_password)
        assert not verify_password('current-pass', session.get(User, 1).hashed_password)
        assert session.get(User, 2).hashed_password == 'unused'
    assert client.post('/auth/login/json', json={'username': 'self', 'password': 'changed-pass'}).status_code == 200
    assert client.post('/auth/login/json', json={'username': 'self', 'password': 'current-pass'}).status_code == 401
    app.dependency_overrides.pop(get_current_active_user)
    assert client.post('/auth/me/password', json={'current_password': 'changed-pass', 'new_password': 'another-pass'}).status_code == 401
