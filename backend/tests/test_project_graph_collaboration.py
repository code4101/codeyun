"""Public HTTP/WebSocket acceptance with real JWTs and an isolated SQLite DB."""
import base64
import copy
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import msgpack
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from backend.api.project_graph_files import router
from backend.core.access.auth import create_user_access_token
from backend.core.collaboration import objects as collaboration
from backend.core.project_graph.codec import stage_to_objects, objects_to_stage, import_prg, export_prg
from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit
from backend.db import get_session
from backend.models import User, GraphResource, ResourceAccessGrant, ResourceIdentity, AppSetting

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


def prg():
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('stage.msgpack', msgpack.packb([
            {'_': 'TextNode', 'uuid': A, 'text': 'A', 'details': []},
            {'_': 'TextNode', 'uuid': B, 'text': 'B', 'details': []}], use_bin_type=True))
        archive.writestr('metadata.msgpack', msgpack.packb({'version': 18}, use_bin_type=True))
        archive.writestr('attachments/test.png', b'original-image')
        archive.writestr('unknown-extra.bin', b'future-compatible')
    return result.getvalue()


@pytest.fixture
def api(tmp_path):
    collaboration.rooms.clear()
    engine = create_engine(f'sqlite:///{tmp_path / "collaboration.db"}', connect_args={'check_same_thread': False})
    for model in (User, GraphResource, ResourceAccessGrant, ResourceIdentity, AppSetting,
                  ObjectHead, ObjectValue, ObjectCommit):
        model.__table__.create(engine)
    users = [User(id=i, username=f'user{i}', nickname=f'协作者{i}', hashed_password='') for i in range(1, 5)]
    with Session(engine) as db:
        db.add_all(users)
        db.commit()
        tokens = {user.id: create_user_access_token(user) for user in users}
    app = FastAPI()
    app.include_router(router, prefix='/files')
    def sessions():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        yield client, tokens
    engine.dispose()
    collaboration.rooms.clear()


def headers(tokens, user=1):
    return {'Authorization': f'Bearer {tokens[user]}'}


def prepare(client, tokens):
    file = client.post('/files', headers=headers(tokens), json={'title': '共同编辑', 'content': base64.b64encode(prg()).decode()}).json()
    rid = file['id']
    for user, role in [(2, 'editor'), (3, 'viewer')]:
        assert client.put(f'/files/{rid}/access', headers=headers(tokens), json={'userId': user, 'role': role}).status_code == 200
    response = client.post(f'/files/{rid}/collaboration', headers=headers(tokens), json={'expectedRevision': file['revision']})
    assert response.status_code == 200, response.text
    return rid, response.json()['objects']


def receive(socket, kind, request_id=None):
    for _ in range(100):
        message = socket.receive_json()
        if message['type'] == kind and (request_id is None or message.get('id') == request_id):
            return message
    raise AssertionError(f'Missing {kind}:{request_id}')


def join(socket, token):
    socket.send_json({'type': 'auth', 'token': token})
    return receive(socket, 'joined')


def lock(socket, *objects):
    socket.send_json({'type': 'acquire', 'id': 'lock', 'objects': list(objects)})
    return receive(socket, 'locked', 'lock')['tokens']


def test_real_auth_leases_commit_retry_reconnect_and_baseline_export(api):
    c, tokens = api
    rid, objects = prepare(c, tokens)
    url = f'/files/{rid}/collaboration/socket'
    with c.websocket_connect(url) as one, c.websocket_connect(url) as two:
        first = join(one, tokens[1]); join(two, tokens[2])
        lease = lock(one, A)
        two.send_json({'type': 'acquire', 'id': 'collision', 'objects': [A, B]})
        assert receive(two, 'error', 'collision')['code'] == 423
        # Failed batch must not take its otherwise-free object B.
        other = lock(one, B)
        before = objects[A]
        after = {**before, 'text': 'owner edit'}
        command = dict(type='commit', id='save', mutationId='stable-retry', tokens=lease,
                       changes=[dict(id=A, before=before, after=after)])
        one.send_json(command)
        assert receive(one, 'commit', 'save')['revision'] == 1
        assert receive(two, 'commit')['changes'][0]['after'] == after
        one.send_json(command)
        assert receive(one, 'commit', 'save')['revision'] == 1
        one.send_json({**command, 'mutationId': 'wrong-generation', 'tokens': {A: 'old-token'}})
        assert receive(one, 'error', 'save')['code'] == 423
        assert c.delete(f'/files/{rid}/collaboration', headers=headers(tokens)).status_code == 409
    # Disconnect frees leases; a new connection obtains a fresh generation.
    with c.websocket_connect(url) as two:
        joined = join(two, tokens[2])
        assert joined['objects'][A]['text'] == 'owner edit'
        assert lock(two, A)[A] != lease[A]
    changes = c.get(f'/files/{rid}/collaboration?after=0', headers=headers(tokens, 2)).json()
    assert len(changes['operations']) == 1
    assert c.put(f'/files/{rid}/content', headers=headers(tokens), json={
        'content': base64.b64encode(prg()).decode(), 'expectedRevision': 3}).status_code == 409
    current = c.get(f'/files/{rid}', headers=headers(tokens)).json()
    assert import_prg(base64.b64decode(current['content']))[A]['text'] == 'owner edit'
    assert c.delete(f'/files/{rid}/collaboration', headers=headers(tokens)).status_code == 200
    restored = c.get(f'/files/{rid}', headers=headers(tokens)).json()
    assert restored['collaborative'] is False
    assert import_prg(base64.b64decode(restored['content']))[A]['text'] == 'owner edit'
    assert c.put(f'/files/{rid}/content', headers=headers(tokens), json={
        'content': restored['content'], 'expectedRevision': restored['revision']}).status_code == 200


def test_viewer_and_unauthorized_cannot_mutate(api):
    c, tokens = api
    rid, _ = prepare(c, tokens)
    assert c.get(f'/files/{rid}/collaboration', headers=headers(tokens, 4)).status_code == 404
    with c.websocket_connect(f'/files/{rid}/collaboration/socket') as viewer:
        assert join(viewer, tokens[3])['role'] == 'viewer'
        viewer.send_json({'type': 'acquire', 'id': 'no-write', 'objects': [A]})
        assert receive(viewer, 'error', 'no-write')['code'] == 403
    with c.websocket_connect(f'/files/{rid}/collaboration/socket') as stranger:
        stranger.send_json({'type': 'auth', 'token': tokens[4]})
        assert receive(stranger, 'error')['code'] == 404


def test_lease_expiry_and_disjoint_concurrent_objects(api, monkeypatch):
    c, tokens = api
    rid, objects = prepare(c, tokens)
    monkeypatch.setattr(collaboration, 'LEASE_SECONDS', .1)
    url = f'/files/{rid}/collaboration/socket'
    with c.websocket_connect(url) as one, c.websocket_connect(url) as two:
        join(one, tokens[1]); join(two, tokens[2])
        expired = lock(one, A)
        time.sleep(.15)
        replacement = lock(two, A)
        one.send_json(dict(type='commit', id='expired', mutationId='expired', tokens=expired,
            changes=[dict(id=A, before=objects[A], after={**objects[A], 'text': 'lost lease'})]))
        assert receive(one, 'error', 'expired')['code'] == 423
        # Hold two independent objects and issue changes without a document lock.
        monkeypatch.setattr(collaboration, 'LEASE_SECONDS', 15)
        replacement = lock(two, A)
        other = lock(one, B)
        def submit(ws, key, lease):
            ws.send_json(dict(type='commit', id=key, mutationId=key, tokens=lease,
                changes=[dict(id=key, before=objects[key], after={**objects[key], 'text': key})]))
            return receive(ws, 'commit', key)
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(submit, one, B, other)
            f2 = pool.submit(submit, two, A, replacement)
            assert sorted([f1.result()['revision'], f2.result()['revision']]) == [1, 2]
    state = c.get(f'/files/{rid}/collaboration', headers=headers(tokens)).json()
    assert state['objects'][A]['text'] == A and state['objects'][B]['text'] == B


def test_serializer_nested_first_occurrence_and_unknown_archive_members():
    # The native serializer can define a node inside an edge before placing a
    # path reference in the stage array. Flattening must not silently drop it.
    stage = [{'_': 'LineEdge', 'uuid': B, 'associationList': [{'_': 'TextNode', 'uuid': A, 'text': 'nested'}]}, {'$': '/0/associationList/0'}]
    mapped = stage_to_objects(stage)
    assert mapped['@order']['value'] == [B, A]
    assert mapped[B]['associationList'] == [{'$graphRef': A}]
    assert stage_to_objects(objects_to_stage(mapped)) == mapped
    content = prg()
    objects = import_prg(content)
    objects[A]['text'] = 'new'
    exported = export_prg(content, objects)
    assert import_prg(exported) == objects
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert archive.read('unknown-extra.bin') == b'future-compatible'
        assert archive.read('attachments/test.png') == b'original-image'


def test_referenced_node_delete_requires_atomic_reference_cleanup(api):
    c, tokens = api
    rid, objects = prepare(c, tokens)
    with c.websocket_connect(f'/files/{rid}/collaboration/socket') as ws:
        join(ws, tokens[1])
        leases = lock(ws, A, B, '@order')
        linked = {**objects[B], 'parent': {'$graphRef': A}}
        ws.send_json(dict(type='commit', id='link', mutationId='link', tokens=leases,
            changes=[dict(id=B, before=objects[B], after=linked)]))
        receive(ws, 'commit', 'link')
        deletion = [dict(id=A, before=objects[A], after=None),
            dict(id='@order', before=objects['@order'], after={'value': [B]})]
        ws.send_json(dict(type='commit', id='delete', mutationId='delete', tokens=leases, changes=deletion))
        assert receive(ws, 'error', 'delete')['code'] == 409
        state = c.get(f'/files/{rid}/collaboration', headers=headers(tokens)).json()
        assert state['revision'] == 1 and A in state['objects']
        # The rejected batch changed neither persisted state nor the derived cache.
        ws.send_json(dict(type='commit', id='delete', mutationId='delete', tokens=leases,
            changes=deletion + [dict(id=B, before=linked, after=objects[B])]))
        assert receive(ws, 'commit', 'delete')['revision'] == 2
    state = c.get(f'/files/{rid}/collaboration', headers=headers(tokens)).json()
    assert A not in state['objects'] and state['objects'][B] == objects[B]
