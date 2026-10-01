"""PG provider for CodeYun collaboration: UUID/reference/order invariants only.

Object leases, durable commits, authentication and socket delivery are shared
with future document providers. PRG archive conversion stays in codec.py.
"""
import time
from typing import Any
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.orm import load_only
from sqlmodel import Session
from backend.models import GraphResource, User
from backend.core.resources.catalog import resource_role, ROLE_RANK
from backend.core.project_graph.gallery import validate_gallery
from backend.core.collaboration.objects import (
    ObjectRoom, read_objects, rooms, object_size, MAX_OBJECTS, MAX_BYTES,
)

def graph_role(session: Session, resource_id: int, user: User) -> str:
    entry = session.get(GraphResource, resource_id, options=[load_only(
        GraphResource.id, GraphResource.owner_id, GraphResource.deleted, GraphResource.kind)])
    if not entry or entry.deleted or entry.kind != 'document' or not user.is_active:
        raise HTTPException(404, '文件不存在')
    role = resource_role(session, 'project_graph', resource_id, entry.owner_id, user)
    if not ROLE_RANK.get(role, 0):
        raise HTTPException(404, '文件不存在')
    return role


def references(value: Any) -> set[str]:
    if isinstance(value, list):
        return set().union(*(references(item) for item in value))
    if isinstance(value, dict):
        if '$graphRef' in value:
            return {str(value['$graphRef'])}
        return set().union(*(references(item) for item in value.values()))
    return set()


def validate_value(key: str, value: dict, objects: dict) -> None:
    if not key or len(key) > 200 or not isinstance(value, dict):
        raise HTTPException(422, '无效图对象')
    if not key.startswith('@') and value.get('uuid') != key:
        raise HTTPException(422, '图对象身份不一致')
    if not key.startswith('@gallery:') and references(value) - objects.keys():
        raise HTTPException(409, '操作会留下悬空连线或分组引用，请连同关联对象一起修改')


def validate_order(objects: dict) -> None:
    order = objects.get('@order', {}).get('value')
    if not isinstance(order, list) or any(not isinstance(key, str) for key in order) or len(order) != len(set(order)) or set(order) != {key for key in objects if not key.startswith('@')}:
        raise HTTPException(422, '对象顺序与对象集合不一致')


def validate_objects(objects: dict[str, dict]) -> None:
    if len(objects) > MAX_OBJECTS or sum(object_size(key, value) for key, value in objects.items()) + 2 > MAX_BYTES:
        raise HTTPException(413, '图文件超过协作容量限制')
    for key, value in objects.items():
        validate_value(key, value, objects)
    validate_order(objects)
    validate_gallery(objects)


class GraphProvider:
    role = staticmethod(graph_role)

    def validate(self, objects: dict, changes: list[dict]) -> None:
        for change in changes:
            if change['after'] is not None:
                validate_value(change['id'], change['after'], objects)
        # Deletion may invalidate references in otherwise unchanged objects.
        if any(change['after'] is None for change in changes):
            for key, value in objects.items():
                validate_value(key, value, objects)
        validate_order(objects)
        validate_gallery(objects)

    def touch(self, session: Session, resource_id: int) -> None:
        session.execute(update(GraphResource).where(GraphResource.id == resource_id).values(
            revision=GraphResource.revision + 1, updated_at=time.time()))

provider = GraphProvider()

class GraphRoom(ObjectRoom):
    def __init__(self, resource_id: int):
        super().__init__(resource_id, provider)
