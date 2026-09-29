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
        z.writestr('stage.msgpack', value)
    return base64.b64encode(out.getvalue()).decode()


def create_daily(c, day):
    return c.post(f'/files/journals/{day}/content', json={'content': prg(msgpack.packb([{'uuid': 'node-1', 'text': 'idea'}])), 'expectedRevision': 0})


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


def test_journal_concurrency_ownership_and_name_collision(library):
    c, owner, _ = library
    ordinary = c.post('/files', json={'title': '2026-09-29', 'content': prg()}).json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create_daily(c, '2026-09-29'), range(8)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert len({r.json()['id'] for r in results}) == 1
    daily = results[0].json()
    assert daily['id'] != ordinary['id']
    assert daily['journalDate'] == '2026-09-29'
    assert c.get(f"/files/{ordinary['id']}").json()['content'] == prg()
    owner['id'] = 2
    assert create_daily(c, '2026-09-29').json()['id'] != daily['id']
    assert c.post('/files/journals/2026-02-30').status_code == 422


def test_journal_date_survives_edits_and_can_be_changed_cleared_deleted(library):
    c, _, _ = library
    daily = create_daily(c, '2026-09-29').json()
    rid = daily['id']
    folder = c.post('/files', json={'title': 'folder', 'kind': 'folder'}).json()['id']
    assert c.patch(f'/files/{rid}', json={'title': '工作台', 'parentId': folder}).json()['journalDate'] == '2026-09-29'
    saved = c.put(f'/files/{rid}/content', json={'content': prg(), 'expectedRevision': 1}).json()
    assert saved['journalDate'] == '2026-09-29'
    assert c.post('/files/journals/2026-09-29').json()['id'] == rid
    assert c.patch(f'/files/{rid}', json={'journalDate': '2026-09-28'}).json()['journalDate'] == '2026-09-28'
    other = create_daily(c, '2026-09-29').json()['id']
    assert c.patch(f'/files/{other}', json={'journalDate': '2026-09-28'}).status_code == 409
    assert c.get(f'/files/{other}').json()['journalDate'] == '2026-09-29'
    assert c.patch(f'/files/{folder}', json={'journalDate': '2026-09-27'}).status_code == 422
    assert c.patch(f'/files/{rid}', json={'journalDate': None}).json()['journalDate'] is None
    assert c.get(f'/files/{rid}').json()['content'] == prg()
    assert c.delete(f'/files/{other}').status_code == 200
    assert create_daily(c, '2026-09-29').json()['id'] != other
    entries = c.get('/files').json()['entries']
    assert len([row for row in entries if row['journalDate'] == '2026-09-29']) == 1


def test_journal_migration_preserves_legacy_rows(tmp_path):
    from sqlalchemy import text
    from backend.migrations.manager import v116_add_graph_journal_date
    engine = create_engine(f'sqlite:///{tmp_path / "old-pg.db"}')
    with Session(engine) as session:
        session.execute(text('CREATE TABLE graphresource (id INTEGER PRIMARY KEY, owner_id INTEGER, title VARCHAR, content BLOB)'))
        session.execute(text("INSERT INTO graphresource VALUES (1, 1, 'd260917', X'0102')"))
        v116_add_graph_journal_date(session)
        v116_add_graph_journal_date(session)
        session.commit()
        assert session.execute(text('SELECT title, content, journal_date FROM graphresource')).one() == ('d260917', b'\x01\x02', None)
    engine.dispose()


def test_empty_journal_preview_never_allocates_and_conflict_preserves_content(library):
    c, _, _ = library
    assert c.post('/files/journals/2026-09-29').json() is None
    for _ in range(3):
        response = c.post('/files/journals/2026-09-29/content', json={'content': prg(msgpack.packb([])), 'expectedRevision': 0})
        assert response.status_code == 200 and response.json() is None
    assert c.get('/files').json()['entries'] == []
    saved = create_daily(c, '2026-09-29').json()
    conflict = c.post('/files/journals/2026-09-29/content', json={'content': prg(msgpack.packb([{'uuid': 'other'}])), 'expectedRevision': 0})
    assert conflict.status_code == 409
    assert c.get(f"/files/{saved['id']}").json()['revision'] == 1


def test_native_empty_canvas_reference_container_is_not_authored_content():
    from backend.core.project_graph.codec import has_prg_content
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr('stage.msgpack', msgpack.packb([]))
        z.writestr('reference.msgpack', msgpack.packb({'sections': {}, 'files': []}))
        z.writestr('metadata.msgpack', msgpack.packb({'version': '2.7.0'}))
    assert not has_prg_content(out.getvalue())


def test_empty_cleanup_is_revision_guarded_and_preserves_authored_records(library):
    c, _, _ = library
    empty = c.post('/files', json={'title': 'empty'}).json()['id']
    c.patch(f'/files/{empty}', json={'journalDate': '2026-09-28'})
    assert c.delete(f'/files/{empty}?onlyIfEmpty=true&expectedRevision=1').status_code == 409
    assert c.delete(f'/files/{empty}?onlyIfEmpty=true&expectedRevision=0').status_code == 200
    authored = create_daily(c, '2026-09-29').json()['id']
    assert c.delete(f'/files/{authored}?onlyIfEmpty=true&expectedRevision=1').status_code == 409
    assert c.get(f'/files/{authored}').status_code == 200
