import base64
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from backend.api.project_graph_files import router
from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import AppSetting, GraphResource, ResourceAccessGrant, ResourceIdentity, User
from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit


def prg(value=b'example'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('stage.msgpack', value)
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def library(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "pg.db"}', connect_args={'check_same_thread': False})
    for model in [User, ResourceIdentity, ResourceAccessGrant, AppSetting, GraphResource, ObjectHead, ObjectValue, ObjectCommit]:
        model.__table__.create(engine)
    with Session(engine) as s:
        s.add(ResourceIdentity(id=50000, resource_type='workbook', legacy_pk='attendance'))
        s.add_all([
            User(id=1, username='owner', nickname='Owner', hashed_password=''),
            User(id=2, username='viewer', nickname='Viewer', hashed_password=''),
            User(id=3, username='editor', nickname='Editor', hashed_password=''),
            User(id=4, username='stranger', nickname='Stranger', hashed_password=''),
        ])
        s.commit()
    app = FastAPI()
    app.include_router(router, prefix='/files')
    current = {'id': 1}

    def sessions():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_active_user] = lambda: User(id=current['id'], username='session', hashed_password='')
    with TestClient(app) as client:
        yield client, current, engine
    engine.dispose()


def grants_for(engine, resource_id):
    with Session(engine) as s:
        return s.exec(select(ResourceAccessGrant)
            .where(ResourceAccessGrant.resource_id == str(resource_id))).all()


def test_access_endpoints_are_owner_only_and_validate_targets(library):
    c, current, _ = library
    rid = c.post('/files', json={'title': 'doc.prg', 'content': prg()}).json()['id']
    folder = c.post('/files', json={'title': 'folder', 'kind': 'folder'}).json()['id']

    access = c.get(f'/files/{rid}/access').json()
    assert access == {'owner': {'id': 1, 'username': 'owner', 'nickname': 'Owner'}, 'grants': []}

    assert c.put(f'/files/{rid}/access', json={'userId': 1, 'role': 'viewer'}).status_code == 422
    assert c.put(f'/files/{rid}/access', json={'userId': 999, 'role': 'viewer'}).status_code == 404
    assert c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'manager'}).status_code == 422
    assert c.put(f'/files/{folder}/access', json={'userId': 2, 'role': 'viewer'}).status_code == 422
    assert c.get(f'/files/{folder}/access').status_code == 422

    current['id'] = 2
    assert c.get(f'/files/{rid}/access').status_code == 404
    assert c.put(f'/files/{rid}/access', json={'userId': 3, 'role': 'editor'}).status_code == 404


def test_single_user_update_never_overwrites_other_grants(library):
    c, _owner, engine = library
    rid = c.post('/files', json={'title': 'doc.prg', 'content': prg()}).json()['id']
    assert c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'viewer'}).status_code == 200
    result = c.put(f'/files/{rid}/access', json={'userId': 3, 'role': 'editor'}).json()
    assert {(g['userId'], g['role']) for g in result['grants']} == {(2, 'viewer'), (3, 'editor')}
    assert {g['username'] for g in result['grants']} == {'viewer', 'editor'}
    result = c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'editor'}).json()
    assert {(g['userId'], g['role']) for g in result['grants']} == {(2, 'editor'), (3, 'editor')}
    assert len(grants_for(engine, rid)) == 2


def test_shared_list_and_role_enforcement(library):
    c, current, _ = library
    folder = c.post('/files', json={'title': 'hidden-folder', 'kind': 'folder'}).json()['id']
    rid = c.post('/files', json={'title': 'doc.prg', 'content': prg(), 'parentId': folder}).json()['id']
    assert c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'viewer'}).status_code == 200
    assert c.put(f'/files/{rid}/access', json={'userId': 3, 'role': 'editor'}).status_code == 200

    current['id'] = 2
    listed = {r['id']: r for r in c.get('/files').json()['entries']}
    assert listed[rid]['shared'] is True
    assert listed[rid]['parentId'] == 0
    assert listed[rid]['ownerId'] == 1
    assert listed[rid]['role'] == 'viewer'
    assert folder not in listed
    assert c.get(f'/files/{folder}').status_code == 404
    assert c.get(f'/files/{rid}').status_code == 200
    assert c.get(f'/files/{rid}').json()['parentId'] == 0
    assert c.get(f'/files/{rid}').json()['role'] == 'viewer'
    assert c.put(f'/files/{rid}/content', json={'content': prg(b'x'), 'expectedRevision': 1}).status_code == 404
    assert c.patch(f'/files/{rid}', json={'title': 'steal'}).status_code == 404
    assert c.delete(f'/files/{rid}').status_code == 404

    current['id'] = 3
    assert c.get(f'/files/{rid}').json()['role'] == 'editor'
    assert c.get(f'/files/{rid}').status_code == 200
    assert c.put(f'/files/{rid}/content', json={'content': prg(b'editor'), 'expectedRevision': 1}).json()['revision'] == 2
    assert c.put(f'/files/{rid}/content', json={'content': prg(b'stale'), 'expectedRevision': 1}).status_code == 409
    assert c.patch(f'/files/{rid}', json={'title': 'steal'}).status_code == 404

    current['id'] = 4
    assert c.get('/files').json()['entries'] == []
    assert c.get(f'/files/{rid}').status_code == 404


def test_revoke_writes_deny_and_blocks_anonymous_fallback(library):
    c, current, engine = library
    rid = c.post('/files', json={'title': 'doc.prg', 'content': prg()}).json()['id']
    c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'viewer'})
    current['id'] = 2
    assert c.get(f'/files/{rid}').status_code == 200

    current['id'] = 1
    revoked = c.put(f'/files/{rid}/access', json={'userId': 2, 'role': 'deny'}).json()
    assert [(g['userId'], g['role']) for g in revoked['grants']] == [(2, 'deny')]
    assert grants_for(engine, rid)[0].role == 'deny'
    with Session(engine) as s:
        s.add(ResourceAccessGrant(resource_type='project_graph', resource_id=str(rid),
            subject_key='anonymous', subject_type='anonymous', role='viewer'))
        s.commit()

    current['id'] = 2
    assert c.get(f'/files/{rid}').status_code == 404
    assert c.put(f'/files/{rid}/content', json={'content': prg(), 'expectedRevision': 1}).status_code == 404
    assert c.get('/files').json()['entries'] == []
    with Session(engine) as s:
        assert s.get(GraphResource, rid).owner_id == 1
