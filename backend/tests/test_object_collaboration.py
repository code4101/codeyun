"""A second, non-PG provider proves the shared protocol has no graph dependency.

This is a disposable paragraph document, not a migration of production notes.
It uses real CodeYun tokens and the existing resource grant authority.
"""
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlmodel import SQLModel, Field, Session, create_engine

from backend.core.access.auth import create_user_access_token
from backend.core.resources.catalog import resource_role
from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit, ObjectRoom, rooms, snapshot
from backend.core.collaboration.socket import serve_socket
from backend.models import User, ResourceAccessGrant


class ParagraphFixture(SQLModel, table=True):
    id: int = Field(primary_key=True)
    revision: int = 0


class ParagraphProvider:
    def role(self, session, resource_id, user):
        role = resource_role(session, 'note', resource_id, 1, user)
        if not session.get(ParagraphFixture, resource_id) or role == 'deny':
            raise HTTPException(404, '文档不存在')
        return role

    def validate(self, objects, changes):
        if any(not isinstance(value.get('text'), str) for value in objects.values()):
            raise HTTPException(422, '段落需要文本')

    def touch(self, session, resource_id):
        session.execute(update(ParagraphFixture).where(ParagraphFixture.id == resource_id).values(
            revision=ParagraphFixture.revision + 1))


def receive(ws, kind, identity=None):
    for _ in range(50):
        value = ws.receive_json()
        if value['type'] == kind and (identity is None or value.get('id') == identity):
            return value
    raise AssertionError(f'missing {kind}')


def test_paragraph_provider_atomicity_document_isolation_and_cold_recovery(tmp_path):
    database = f'sqlite:///{tmp_path / "paragraph.db"}'
    engine = create_engine(database, connect_args={'check_same_thread': False})
    for model in (ParagraphFixture, ObjectHead, ObjectValue, ObjectCommit, User, ResourceAccessGrant):
        model.__table__.create(engine)
    with Session(engine) as db:
        owner = User(id=1, username='paragraph-owner', hashed_password='')
        db.add(owner)
        for rid in (9001, 9002):
            db.add(ParagraphFixture(id=rid))
            db.add(ObjectHead(resource_id=rid))
            db.add(ObjectValue(resource_id=rid, object_id='paragraph-1', value={'text': 'original'}))
        db.commit()
        token = create_user_access_token(owner)
    app = FastAPI()
    provider = ParagraphProvider()
    @app.websocket('/documents/{rid}')
    async def socket(ws: WebSocket, rid: int):
        with Session(engine) as db:
            await serve_socket(ws, rid, db, rooms.setdefault(rid, ObjectRoom(rid, provider)))
    with TestClient(app) as client:
        with client.websocket_connect('/documents/9001') as one, client.websocket_connect('/documents/9002') as two:
            for ws in (one, two):
                ws.send_json({'type': 'auth', 'token': token})
                receive(ws, 'joined')
                ws.send_json({'type': 'acquire', 'id': 'lease', 'objects': ['paragraph-1']})
            lease = receive(one, 'locked')['tokens']
            assert receive(two, 'locked')['tokens'] != lease  # Same object ID, different document.
            command = dict(type='commit', id='write', mutationId='receipt', tokens=lease,
                changes=[dict(id='paragraph-1', before={'text': 'original'}, after={'text': 'saved'})])
            # A provider rejection must change neither object/cache nor resource revision.
            one.send_json({**command, 'changes': [dict(id='paragraph-1', before={'text': 'original'}, after={'text': 42})]})
            assert receive(one, 'error', 'write')['code'] == 422
            one.send_json(command)
            assert receive(one, 'commit', 'write')['revision'] == 1
            one.send_json({**command, 'mutationId': 'stale'})
            assert receive(one, 'error', 'write')['code'] == 409
    # Close every DB connection and recreate both engine and room authority.
    engine.dispose()
    rooms.clear()
    engine = create_engine(database, connect_args={'check_same_thread': False})
    with Session(engine) as db:
        assert snapshot(db, 9001)['objects'] == {'paragraph-1': {'text': 'saved'}}
        assert snapshot(db, 9002)['objects'] == {'paragraph-1': {'text': 'original'}}
        assert db.get(ParagraphFixture, 9001).revision == 1
    with TestClient(app) as client, client.websocket_connect('/documents/9001') as ws:
        ws.send_json({'type': 'auth', 'token': token, 'pendingMutationIds': ['receipt']})
        joined = receive(ws, 'joined')
        assert joined['accepted'] == ['receipt']
        ws.send_json({'type': 'acquire', 'id': 'lease', 'objects': ['paragraph-1']})
        assert receive(ws, 'locked')['tokens'] != lease
        # A retry after cold recovery resolves the receipt without a second write.
        ws.send_json(command)
        assert receive(ws, 'commit', 'write')['revision'] == 1
    engine.dispose()
    rooms.clear()
