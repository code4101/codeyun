"""Authenticated PG collaboration endpoint; no token in URLs or presence data.

Enable is an explicit owner action after saving the ordinary PRG. The HTTP
snapshot and ordered operation log are the recovery authority. WebSocket
messages carry ephemeral leases/presence and durably acknowledged object edits.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import delete, update
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.core.project_graph.codec import import_prg, export_prg
from backend.core.project_graph.collaboration import (
    GraphRoom, graph_role, validate_objects,
)
from backend.core.collaboration.objects import ObjectHead, ObjectValue, ObjectCommit, read_objects, rooms, snapshot
from backend.core.collaboration.socket import serve_socket
from backend.db import get_session
from backend.models import GraphResource, User

router = APIRouter()


class EnableCollaboration(BaseModel):
    expectedRevision: int = Field(ge=0)


@router.get('/{resource_id}/collaboration')
async def read_collaboration(resource_id: int, after: int | None = None,
                       session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    role = graph_role(session, resource_id, user)
    room = rooms.setdefault(resource_id, GraphRoom(resource_id))
    async with room.guard:
        session.expire_all()
        if after is None:
            return {**snapshot(session, resource_id), 'role': role}
        head = session.get(ObjectHead, resource_id)
        if not head or after < 0 or after > head.revision:
            raise HTTPException(409, '协作版本无效，请重新读取完整快照')
        rows = session.exec(select(ObjectCommit).where(ObjectCommit.resource_id == resource_id,
            ObjectCommit.revision > after).order_by(ObjectCommit.revision).limit(500)).all()
        return dict(revision=head.revision, nextRevision=rows[-1].revision if rows else after,
            hasMore=bool(rows and rows[-1].revision < head.revision), operations=[dict(revision=row.revision,
            mutationId=row.mutation_id, changes=row.changes) for row in rows])


@router.post('/{resource_id}/collaboration')
async def enable_collaboration(resource_id: int, body: EnableCollaboration,
                               session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    if graph_role(session, resource_id, user) != 'manager':
        raise HTTPException(403, '只有所有者可以启用协作')
    room = rooms.setdefault(resource_id, GraphRoom(resource_id))
    async with room.guard:
        if session.get(ObjectHead, resource_id):
            return snapshot(session, resource_id)
        entry = session.get(GraphResource, resource_id)
        objects = import_prg(entry.content)
        validate_objects(objects)
        # Changing the ordinary revision fences out legacy saves already in flight.
        result = session.execute(update(GraphResource).where(GraphResource.id == resource_id,
            GraphResource.revision == body.expectedRevision).values(revision=body.expectedRevision + 1))
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(409, '文件已有新版本，请刷新后启用协作')
        session.add(ObjectHead(resource_id=resource_id))
        session.add_all([ObjectValue(resource_id=resource_id, object_id=key, value=value) for key, value in objects.items()])
        session.commit()
        return snapshot(session, resource_id)


@router.delete('/{resource_id}/collaboration')
async def disable_collaboration(resource_id: int, session: Session = Depends(get_session),
                                user: User = Depends(get_current_active_user)):
    if graph_role(session, resource_id, user) != 'manager':
        raise HTTPException(403, '只有所有者可以结束协作')
    room = rooms.setdefault(resource_id, GraphRoom(resource_id))
    async with room.guard:
        if room.peers:
            raise HTTPException(409, '请所有协作者关闭此文件后再结束协作')
        entry = session.get(GraphResource, resource_id)
        if session.get(ObjectHead, resource_id):
            entry.content = export_prg(entry.content, read_objects(session, resource_id))
            entry.revision += 1
            entry.updated_at = time.time()
            session.add(entry)
            for model in (ObjectValue, ObjectCommit, ObjectHead):
                session.execute(delete(model).where(model.resource_id == resource_id))
            session.commit()
        return dict(enabled=False, revision=entry.revision)


@router.websocket('/{resource_id}/collaboration/socket')
async def collaborate(socket: WebSocket, resource_id: int, session: Session = Depends(get_session)):
    await serve_socket(socket, resource_id, session, rooms.setdefault(resource_id, GraphRoom(resource_id)))
