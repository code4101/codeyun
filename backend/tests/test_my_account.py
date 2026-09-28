import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine
from backend.api.auth import router
from backend.core.access.auth import get_current_active_user, get_password_hash, verify_password
from backend.db import get_session
from backend.models import User


@pytest.fixture
def account(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.core.access import avatars
    monkeypatch.setattr(avatars, 'get_settings', lambda: SimpleNamespace(data_dir=tmp_path))
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


def test_avatar_upload_normalization_ownership_and_reset(account):
    from io import BytesIO
    from PIL import Image
    from backend.core.access.avatars import avatar_path, MAX_AVATAR_BYTES
    client, engine, app = account
    assert client.get('/auth/me').json()['avatar_url'] is None
    original = BytesIO()
    Image.new('RGB', (600, 400), 'red').save(original, format='PNG')
    uploaded = client.post('/auth/me/avatar', files={'file': ('avatar.png', original.getvalue(), 'image/png')})
    assert uploaded.status_code == 200
    url = uploaded.json()['avatar_url'].removeprefix('/api')
    image = client.get(url)
    assert image.status_code == 200 and image.headers['content-type'] == 'image/webp'
    with Image.open(BytesIO(image.content)) as normalized:
        assert normalized.size == (256, 256)
        assert not normalized.getexif()
    assert not avatar_path(2).exists()
    assert client.patch('/auth/me', json={'username': 'renamed'}).json()['avatar_url'] == uploaded.json()['avatar_url']
    for content in [b'<svg onload="bad"/>', b'x' * (MAX_AVATAR_BYTES + 1)]:
        invalid = client.post('/auth/me/avatar', files={'file': ('fake.png', content, 'image/png')})
        assert invalid.status_code == 422
        assert client.get(url).content == image.content
    assert client.delete('/auth/me/avatar').json()['avatar_url'] is None
    assert client.get(url).status_code == 404
    assert client.get('/auth/avatars/-1').status_code == 404
    app.dependency_overrides.pop(get_current_active_user)
    assert client.post('/auth/me/avatar', files={'file': ('avatar.png', original.getvalue(), 'image/png')}).status_code == 401
    assert client.delete('/auth/me/avatar').status_code == 401


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


def test_password_confirmation_strength_and_login(account):
    client, engine, app = account
    for weak in ['1234567890', 'Password123!', 'abcdefghijA1!', 'aaaaaaaaAAAA11!', 'self-LongSecret!39', 'short', '字' * 25]:
        response = client.post('/auth/me/password', json={'new_password': weak, 'confirm_password': weak})
        assert response.status_code == 422
    good = 'Fern!River7Quartz'
    rating = client.post('/auth/me/password-strength', json={'password': good}).json()
    assert rating['accepted'] and rating['score'] == 4
    assert client.post('/auth/me/password', json={'new_password': good, 'confirm_password': good + 'x'}).status_code == 400
    assert client.post('/auth/me/password', json={'new_password': good}).status_code == 422
    with Session(engine) as session:
        assert verify_password('current-pass', session.get(User, 1).hashed_password)
    response = client.post('/auth/me/password', json={'new_password': good, 'confirm_password': good})
    assert response.status_code == 204 and not response.content
    assert client.post('/auth/login/json', json={'username': 'self', 'password': good}).status_code == 200
    assert client.post('/auth/login/json', json={'username': 'self', 'password': 'current-pass'}).status_code == 401
    app.dependency_overrides.pop(get_current_active_user)
    assert client.post('/auth/me/password', json={'new_password': good, 'confirm_password': good}).status_code == 401
    assert client.post('/auth/me/password-strength', json={'password': good}).status_code == 401


def test_existing_password_warning_and_generated_password(account):
    from backend.core.access.password_strength import assess_password
    client, engine, app = account
    assert client.get('/auth/me').json()['password_needs_reset'] is None
    with Session(engine) as session:
        user = session.get(User, 1)
        user.password_plain = '123456'
        user.hashed_password = get_password_hash('123456')
        session.add(user)
        session.commit()
    profile = client.get('/auth/me').json()
    assert profile['password_needs_reset'] is True
    assert 'password_plain' not in profile and 'hashed_password' not in profile
    generated = set()
    for _ in range(12):
        response = client.post('/auth/me/password-generate')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        candidate = response.json()['password']
        assert len(candidate) == 20
        assert assess_password(candidate, 'self')['accepted']
        generated.add(candidate)
    assert len(generated) == 12
    with Session(engine) as session:
        assert verify_password('123456', session.get(User, 1).hashed_password)
    assert client.post('/auth/me/password', json={'new_password': candidate, 'confirm_password': candidate}).status_code == 204
    assert client.get('/auth/me').json()['password_needs_reset'] is False
    # A stale historical value must not generate a false warning.
    with Session(engine) as session:
        user = session.get(User, 1)
        user.password_plain = '123456'
        session.add(user)
        session.commit()
    assert client.get('/auth/me').json()['password_needs_reset'] is None
    app.dependency_overrides.pop(get_current_active_user)
    assert client.post('/auth/me/password-generate').status_code == 401


def test_rename_session_identity_and_unique_username(account):
    from backend.core.access.auth import create_access_token, get_optional_current_user_from_token
    client, engine, app = account
    app.dependency_overrides.pop(get_current_active_user)
    login = client.post('/auth/login/json', json={'username': 'self', 'password': 'current-pass'})
    headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
    assert client.patch('/auth/me', json={'username': 'other'}, headers=headers).status_code == 409
    for name in ['', 'two names']:
        assert client.patch('/auth/me', json={'username': name}, headers=headers).status_code == 422
    response = client.patch('/auth/me', json={'username': 'renamed'}, headers=headers)
    assert response.status_code == 200 and response.json()['id'] == 1
    assert client.get('/auth/me', headers=headers).json()['username'] == 'renamed'
    with Session(engine) as session:
        session.add(User(id=3, username='self', hashed_password='unused'))
        session.commit()
    assert client.get('/auth/me', headers=headers).json()['id'] == 1
    with Session(engine) as session:
        assert get_optional_current_user_from_token(login.json()['access_token'], session).id == 1
    assert client.get('/auth/me', headers={'Authorization': 'Bearer ' + create_access_token({'sub': 'self'})}).status_code == 401
    assert client.get('/auth/me', headers={'Authorization': 'Bearer ' + create_access_token({'sub': '1'})}).status_code == 401
    assert client.post('/auth/login/json', json={'username': 'renamed', 'password': 'current-pass'}).status_code == 200
