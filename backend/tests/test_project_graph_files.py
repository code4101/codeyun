import base64
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import msgpack
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from backend.api.project_graph_files import router
from backend.core.access.auth import get_current_active_user
from backend.db import get_session
from backend.models import AppSetting, GraphResource, ResourceIdentity, ResourceAccessGrant, User
from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit


def prg(value=b'example'):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('stage.msgpack', msgpack.packb([{'_': 'TextNode', 'uuid': 'fixture',
            'text': value.decode(), 'createdAt': 1000, 'updatedAt': 1000}], use_bin_type=True))
    return base64.b64encode(out.getvalue()).decode()


@pytest.fixture
def library(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "pg.db"}', connect_args={'check_same_thread': False})
    for model in [User, ResourceIdentity, ResourceAccessGrant, AppSetting, GraphResource, ObjectHead, ObjectValue, ObjectCommit]:
        model.__table__.create(engine)
    with Session(engine) as s:
        s.add(ResourceIdentity(id=50000, resource_type='workbook', legacy_pk='attendance'))
        s.commit()
    app = FastAPI()
    app.include_router(router, prefix='/files')
    owner = {'id': 1}
    def sessions():
        with Session(engine) as s:
            yield s
    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_current_active_user] = lambda: User(id=owner['id'], username='test', hashed_password='')
    with TestClient(app) as client:
        yield client, owner, engine
    engine.dispose()


def test_global_ids_owner_isolation_idempotency_and_revision(library):
    c, owner, engine = library
    body = {'title': 'demo.prg', 'content': prg(), 'skipExisting': True}
    first = c.post('/files', json=body).json()
    rid = first['id']
    assert rid > 50000
    assert c.post('/files', json=body).json()['id'] == rid
    assert c.post('/files', json=body).json()['skipped']
    owner['id'] = 2
    assert c.get(f'/files/{rid}').status_code == 404
    assert c.get(f'/files/{rid}/original').status_code == 404
    assert c.put(f'/files/{rid}/content', json={'content': prg(), 'expectedRevision': 1}).status_code == 404
    assert c.patch(f'/files/{rid}', json={'title': 'steal'}).status_code == 404
    assert c.delete(f'/files/{rid}').status_code == 404
    second = c.post('/files', json=body).json()
    assert second['id'] != rid
    assert [r['id'] for r in c.get('/files').json()['entries']] == [second['id']]
    owner['id'] = 1
    changed = prg(b'upgraded')
    assert c.put(f'/files/{rid}/content', json={'content': changed, 'expectedRevision': 1}).json()['revision'] == 2
    assert c.put(f'/files/{rid}/content', json={'content': prg(b'stale'), 'expectedRevision': 1}).status_code == 409
    assert c.get(f'/files/{rid}').json()['content'] == changed
    assert c.get(f'/files/{rid}/original').content == base64.b64decode(body['content'])
    assert c.patch(f'/files/{rid}', json={'title': 'renamed'}).json()['id'] == rid
    assert c.delete(f'/files/{rid}').status_code == 200
    assert c.put(f'/files/{rid}/content', json={'content': changed, 'expectedRevision': 2}).status_code == 404
    with Session(engine) as s:
        assert s.get(ResourceIdentity, 50000).legacy_pk == 'attendance'
        assert s.get(ResourceIdentity, rid) is not None


def test_folders_and_concurrent_import(library):
    c, owner, _ = library
    folder = c.post('/files', json={'title': 'folder', 'kind': 'folder'}).json()['id']
    child = c.post('/files', json={'title': 'child', 'kind': 'folder', 'parentId': folder}).json()['id']
    assert c.patch(f'/files/{folder}', json={'parentId': child}).status_code == 422
    assert c.delete(f'/files/{folder}').status_code == 409
    body = {'title': 'same.prg', 'content': prg(), 'parentId': child, 'skipExisting': True}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: c.post('/files', json=body), range(8)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len({r.json()['id'] for r in results}) == 1
    owner['id'] = 2
    assert c.post('/files', json={'title': 'leak', 'parentId': child}).status_code == 404
    assert c.post('/files', json={'title': '../bad'}).status_code == 422
    assert c.post('/files', json={'title': 'bad', 'content': base64.b64encode(b'invalid').decode()}).status_code == 422


def test_shared_grant_changes_access_without_copying_or_changing_owner(library):
    c, owner, engine = library
    rid = c.post('/files', json={'title': 'shared', 'content': prg()}).json()['id']
    with Session(engine) as s:
        grant = ResourceAccessGrant(resource_type='project_graph', resource_id=str(rid),
            subject_key='user:2', subject_type='user', subject_user_id=2, role='viewer')
        s.add(grant); s.commit()
        grant_id = grant.id
    owner['id'] = 2
    assert c.get(f'/files/{rid}').status_code == 200
    assert c.put(f'/files/{rid}/content', json={'content': prg(), 'expectedRevision': 1}).status_code == 404
    with Session(engine) as s:
        grant = s.get(ResourceAccessGrant, grant_id)
        grant.role = 'editor'; s.add(grant); s.commit()
    assert c.put(f'/files/{rid}/content', json={'content': prg(b'edit'), 'expectedRevision': 1}).status_code == 200
    assert c.delete(f'/files/{rid}').status_code == 404
    with Session(engine) as s:
        assert s.get(GraphResource, rid).owner_id == 1


def test_native_empty_canvas_reference_container_is_not_authored_content():
    from backend.core.project_graph.codec import has_prg_content
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('stage.msgpack', msgpack.packb([]))
        z.writestr('reference.msgpack', msgpack.packb({'sections': {}, 'files': []}))
        z.writestr('metadata.msgpack', msgpack.packb({'version': '2.7.0'}))
    assert not has_prg_content(out.getvalue())


def test_removed_daily_endpoints_and_metadata(library):
    c, _, _ = library
    assert c.post('/files/journals/2026-09-29').status_code == 404
    assert c.post('/files/journals/2026-09-29/content', json={'content': prg(), 'expectedRevision': 0}).status_code == 404
    row = c.post('/files', json={'title': '2026-09-29', 'content': prg()}).json()
    assert 'journalDate' not in row
    assert 'journalDate' not in c.get('/files').json()['entries'][0]


def test_remove_daily_migration_preserves_ordinary_files_and_identity_reservations(library):
    from sqlalchemy import text
    from backend.migrations.manager import v116_add_graph_journal_date, v117_remove_graph_journals
    c, _, engine = library
    ordinary_content = prg()
    ordinary = c.post('/files', json={'title': '2026-09-29', 'content': ordinary_content}).json()['id']
    daily = c.post('/files', json={'title': 'renamed daily', 'content': prg()}).json()['id']
    deleted = c.post('/files', json={'title': 'deleted daily', 'content': prg()}).json()['id']
    with Session(engine) as s:
        v116_add_graph_journal_date(s)
        s.execute(text('UPDATE graphresource SET journal_date = :day WHERE id = :id'), {'day': '2026-09-29', 'id': daily})
        s.execute(text('UPDATE graphresource SET journal_date = :day, deleted = 1 WHERE id = :id'), {'day': '2026-09-28', 'id': deleted})
        for rid in (ordinary, daily, deleted):
            s.add(ResourceAccessGrant(resource_type='project_graph', resource_id=str(rid), subject_key='user:2', subject_type='user', subject_user_id=2, role='viewer'))
            s.add(ObjectHead(resource_id=rid))
            s.add(ObjectValue(resource_id=rid, object_id='node', value={'text': 'content'}))
            s.add(ObjectCommit(resource_id=rid, mutation_id='edit', revision=1, user_id=1, changes=[]))
        s.commit()
        v117_remove_graph_journals(s)
        v117_remove_graph_journals(s)
        s.commit()
        assert s.get(GraphResource, ordinary).content == base64.b64decode(ordinary_content)
        for rid in (daily, deleted):
            assert s.get(GraphResource, rid) is None
            assert s.get(ResourceIdentity, rid) is not None
        for table in ('objecthead', 'objectvalue', 'objectcommit'):
            assert s.execute(text(f'SELECT resource_id FROM {table}')).scalars().all() == [ordinary]
        assert s.execute(text('SELECT resource_id FROM resourceaccessgrant')).scalars().all() == [str(ordinary)]
        assert 'journal_date' not in [row[1] for row in s.execute(text('PRAGMA table_info(graphresource)'))]
