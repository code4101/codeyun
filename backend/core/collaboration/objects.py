"""Shared CodeYun object collaboration (protocol 1).

ResourceIdentity.id is globally unique across document types. Providers define
their object boundaries, domain validation and resource revision update; the
core never imports a graph/note/sheet model. One stable object ID is one lease.
Whole batches validate before writing, and ACK follows the durable transaction.
Mutation IDs are durable receipts; they are not an editor undo stack.

Rooms and leases require ONE backend process. Multi-worker deployment requires
a shared lease authority, not just SQLite CAS. Current CodeYun runs one instance.
"""
from __future__ import annotations
import asyncio
import copy
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol
from weakref import WeakValueDictionary
from fastapi import HTTPException, WebSocket
from sqlalchemy import Column, JSON, update
from sqlmodel import Field, Session, SQLModel, select

LEASE_SECONDS = 15.0
MAX_OBJECTS = 20_000
MAX_BATCH = 500
MAX_BYTES = 16_000_000

class ObjectProvider(Protocol):
    """All methods run inside the room guard. touch participates in our transaction.

    Domain endpoints import/export their formats and fence legacy saves. Socket
    adapters supply role() using CodeYun authentication/resource permissions.
    validate() must not mutate objects. Do not commit inside touch().
    """
    def validate(self, objects: dict[str, dict], changes: list[dict]) -> None: ...
    def touch(self, session: Session, resource_id: int) -> None: ...
    def role(self, session: Session, resource_id: int, user: Any) -> str: ...

class ObjectHead(SQLModel, table=True):
    resource_id: int = Field(primary_key=True)
    revision: int = 0


class ObjectValue(SQLModel, table=True):
    resource_id: int = Field(primary_key=True)
    object_id: str = Field(primary_key=True)
    value: dict = Field(sa_column=Column(JSON, nullable=False))


class ObjectCommit(SQLModel, table=True):
    resource_id: int = Field(primary_key=True)
    mutation_id: str = Field(primary_key=True)
    revision: int = Field(index=True)
    user_id: int
    created_at: float = Field(default_factory=time.time)
    changes: list = Field(sa_column=Column(JSON, nullable=False))


def read_objects(session: Session, resource_id: int) -> dict[str, dict]:
    return {row.object_id: row.value for row in session.exec(
        select(ObjectValue).where(ObjectValue.resource_id == resource_id)).all()}


def object_size(key: str, value: dict) -> int:
    try:
        return len(json.dumps(key, ensure_ascii=False).encode('utf-8')) + len(
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 4
    except (ValueError, TypeError, RecursionError) as exc:
        raise HTTPException(422, '协作对象必须为有限 JSON 数据') from exc


def validate_document(objects: dict[str, dict], sizes: dict[str, int] | None = None) -> None:
    for key, value in objects.items():
        if not isinstance(key, str) or not key or len(key) > 200 or not isinstance(value, dict):
            raise HTTPException(422, '无效协作对象')
    if len(objects) > MAX_OBJECTS or sum((sizes or {k: object_size(k, v) for k, v in objects.items()}).values()) + 2 > MAX_BYTES:
        raise HTTPException(413, '文档超过协作容量限制')

def snapshot(session: Session, resource_id: int) -> dict:
    head = session.get(ObjectHead, resource_id)
    return dict(enabled=head is not None, revision=head.revision if head else 0,
                objects=read_objects(session, resource_id) if head else {})

@dataclass
class Peer:
    socket: WebSocket
    user_id: int
    name: str
    role: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cursor: dict | None = None
    selection: list[str] = field(default_factory=list)
    outbox: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=128))

    def public(self):
        colors = ['#2563eb', '#db2777', '#059669', '#d97706', '#7c3aed', '#0891b2']
        return dict(id=self.id, userId=self.user_id, name=self.name, role=self.role,
                    color=colors[self.user_id % len(colors)], cursor=self.cursor, selection=self.selection)


class ObjectRoom:
    """Bounded room state; client tokens identify lease generations, not users."""
    def __init__(self, resource_id: int, provider: ObjectProvider):
        self.provider = provider
        self.resource_id = resource_id
        self.guard = asyncio.Lock()
        self.peers: dict[str, Peer] = {}
        self.leases: dict[str, dict] = {}
        # Derived cache only: a head revision match is required on every write.
        # Update it after COMMIT, never while validating an unaccepted proposal.
        self.cached_revision = -1
        self.cached_objects: dict | None = None
        self.cached_sizes: dict[str, int] = {}

    def expire(self):
        now = time.monotonic()
        self.leases = {key: lease for key, lease in self.leases.items() if lease['until'] > now}

    def presence(self):
        self.expire()
        return dict(type='presence', peers=[peer.public() for peer in self.peers.values()],
                    locks={key: dict(peerId=l['peer'], expiresIn=max(0, l['until'] - time.monotonic()))
                           for key, l in self.leases.items()})

    def acquire(self, peer: Peer, keys: list[str]):
        self.expire()
        if not isinstance(keys, list) or any(not isinstance(k, str) or not k or len(k) > 200 for k in keys):
            raise HTTPException(422, '无效锁对象集合')
        keys = sorted(set(keys))
        if not keys or len(keys) > MAX_BATCH or any(not isinstance(k, str) or len(k) > 200 for k in keys):
            raise HTTPException(422, '无效锁对象集合')
        for key in keys:
            lease = self.leases.get(key)
            if lease and lease['peer'] != peer.id:
                owner = self.peers.get(lease['peer'])
                raise HTTPException(423, {'message': f'{owner.name if owner else "另一位协作者"} 正在编辑此对象', 'objectId': key})
        result = {}
        for key in keys:
            lease = self.leases.get(key)
            if not lease:
                lease = dict(peer=peer.id, token=uuid.uuid4().hex, until=0)
                self.leases[key] = lease
            lease['until'] = time.monotonic() + LEASE_SECONDS
            result[key] = lease['token']
        return result

    def renew(self, peer: Peer, tokens: dict):
        self.expire()
        valid = {}
        for key, token in tokens.items():
            lease = self.leases.get(key)
            if lease and lease['peer'] == peer.id and lease['token'] == token:
                lease['until'] = time.monotonic() + LEASE_SECONDS
                valid[key] = token
        return valid

    def release(self, peer: Peer, keys: list[str] | None = None):
        for key in list(self.leases):
            if self.leases[key]['peer'] == peer.id and (keys is None or key in keys):
                del self.leases[key]

    def send(self, peer: Peer, message: dict):
        # Each socket has one writer. Slow consumers disconnect and catch up
        # from durable state instead of delaying another user's commit.
        try:
            peer.outbox.put_nowait(message)
        except asyncio.QueueFull:
            self.peers.pop(peer.id, None)
            self.release(peer)
            asyncio.create_task(peer.socket.close(code=1013))

    def broadcast(self, message: dict):
        for peer in list(self.peers.values()):
            self.send(peer, message)

    def commit(self, session: Session, peer: Peer, payload: dict):
        mutation_id = payload.get('mutationId')
        changes = payload.get('changes')
        if not isinstance(mutation_id, str) or not 1 <= len(mutation_id) <= 100 or not isinstance(changes, list) or not 1 <= len(changes) <= MAX_BATCH:
            raise HTTPException(422, '无效操作批次')
        existing = session.get(ObjectCommit, (self.resource_id, mutation_id))
        if existing:
            if existing.user_id != peer.user_id or existing.changes != changes:
                raise HTTPException(409, '操作标识重复但内容不同')
            return dict(type='commit', revision=existing.revision, mutationId=mutation_id, changes=changes)
        self.expire()
        head = session.get(ObjectHead, self.resource_id)
        if head is None:
            raise HTTPException(409, '协作文档尚未初始化')
        if self.cached_objects is None or self.cached_revision != head.revision:
            self.cached_objects = read_objects(session, self.resource_id)
            self.cached_sizes = {key: object_size(key, value) for key, value in self.cached_objects.items()}
            self.cached_revision = head.revision
        objects = self.cached_objects
        next_objects = dict(objects)
        next_sizes = dict(self.cached_sizes)
        seen = set()
        tokens = payload.get('tokens') or {}
        if not isinstance(tokens, dict):
            raise HTTPException(422, '无效锁凭据')
        for change in changes:
            if not isinstance(change, dict):
                raise HTTPException(422, '无效对象变更')
            key = change.get('id')
            if not isinstance(key, str) or key in seen or 'before' not in change or 'after' not in change:
                raise HTTPException(422, '无效对象变更')
            seen.add(key)
            lease = self.leases.get(key)
            if not lease or lease['peer'] != peer.id or lease['token'] != tokens.get(key):
                raise HTTPException(423, '编辑锁已失效，请重新获取后重试')
            if objects.get(key) != change['before']:
                raise HTTPException(409, '对象已有更新，本地修改已保留，请重新读取后合并')
            if change['after'] is None:
                next_objects.pop(key, None)
                next_sizes.pop(key, None)
            else:
                next_objects[key] = change['after']
                next_sizes[key] = object_size(key, change['after'])
        validate_document(next_objects, next_sizes)
        self.provider.validate(next_objects, changes)
        revision = head.revision + 1
        result = session.execute(update(ObjectHead).where(ObjectHead.resource_id == self.resource_id,
            ObjectHead.revision == head.revision).values(revision=revision))
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(409, '协作服务版本变化，请重新连接')
        for change in changes:
            row = session.get(ObjectValue, (self.resource_id, change['id']))
            if change['after'] is None:
                if row:
                    session.delete(row)
            else:
                if row is None:
                    row = ObjectValue(resource_id=self.resource_id, object_id=change['id'], value=change['after'])
                else:
                    row.value = change['after']
                session.add(row)
        session.add(ObjectCommit(resource_id=self.resource_id, mutation_id=mutation_id,
            revision=revision, user_id=peer.user_id, changes=copy.deepcopy(changes)))
        self.provider.touch(session, self.resource_id)
        session.commit()
        self.cached_objects, self.cached_sizes, self.cached_revision = next_objects, next_sizes, revision
        return dict(type='commit', revision=revision, mutationId=mutation_id, changes=changes)


# Requests and sockets hold strong room references while using its guard.
# Idle rooms disappear instead of accumulating one cache per opened document.
rooms: WeakValueDictionary[int, ObjectRoom] = WeakValueDictionary()
