"""Authenticated PG library: global IDs, owner-scoped paths, atomic revision writes.

Import is idempotent by parent/name; it never overwrites an existing file. This API
also serves the local import CLI, so ownership and numbering have one authority.
"""
from __future__ import annotations

import base64
import io
import time
import unicodedata
import uuid
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.core.resources.identity import allocate_resource_id, RESOURCE_TYPE_GRAPH
from backend.core.resources.catalog import resource_role, ROLE_RANK
from backend.db import get_session
from backend.models import AppSetting, GraphResource, User

router = APIRouter()


class CreateEntry(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    kind: Literal['document', 'folder'] = 'document'
    parentId: int = Field(default=0, ge=0)
    content: str = ''
    original: str | None = None
    skipExisting: bool = False


class ChangeEntry(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    parentId: int | None = Field(default=None, ge=0)


class WriteContent(BaseModel):
    content: str
    expectedRevision: int = Field(ge=0)


def normalized_name(title: str, kind: str) -> tuple[str, str]:
    title = unicodedata.normalize('NFC', title.strip())
    if kind == 'document':
        while title.lower().endswith('.prg'):
            title = title[:-4].rstrip()
    if not title or title in {'.', '..'} or any(c in title for c in '/\\\0'):
        raise HTTPException(422, '无效文件名')
    return title, (title + ('.prg' if kind == 'document' else '')).casefold()


def decode_content(encoded: str) -> bytes:
    try:
        if len(encoded) > 90_000_000:
            raise ValueError('文件过大')
        data = base64.b64decode(encoded, validate=True)
        if data:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if 'stage.msgpack' not in archive.namelist():
                    raise ValueError('缺少 stage.msgpack')
                if sum(item.file_size for item in archive.infolist()) > 256_000_000:
                    raise ValueError('解压内容过大')
                if archive.testzip():
                    raise ValueError('ZIP 校验失败')
        return data
    except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise HTTPException(422, f'无效 PRG 文件：{exc}') from exc


def owned(session: Session, user: User, resource_id: int, required: str = 'manager') -> GraphResource:
    entry = session.get(GraphResource, resource_id)
    if not entry or entry.deleted:
        raise HTTPException(404, '文件不存在')
    if ROLE_RANK[resource_role(session, RESOURCE_TYPE_GRAPH, resource_id, entry.owner_id, user)] < ROLE_RANK[required]:
        raise HTTPException(404, '文件不存在')
    return entry


def parent_folder(session: Session, user: User, parent_id: int):
    if parent_id and owned(session, user, parent_id).kind != 'folder':
        raise HTTPException(422, '目标不是文件夹')


def metadata(entry: GraphResource) -> dict:
    return dict(id=entry.id, title=entry.title, kind=entry.kind, parentId=entry.parent_id,
                revision=entry.revision, updatedAt=int(entry.updated_at * 1000))


@router.get('')
def list_entries(session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    rows = session.exec(select(GraphResource.id, GraphResource.title, GraphResource.kind,
        GraphResource.parent_id, GraphResource.revision, GraphResource.updated_at)
        .where(GraphResource.owner_id == user.id, GraphResource.deleted == False)
        .order_by(GraphResource.title)).all()
    claim = session.get(AppSetting, 'project-graph.legacy-browser-owner')
    return {'ownerId': user.id, 'legacyBrowserImport': bool(claim and claim.value.get('owner_id') == user.id), 'entries': [dict(id=r.id, title=r.title, kind=r.kind,
        parentId=r.parent_id, revision=r.revision, updatedAt=int(r.updated_at * 1000)) for r in rows]}


@router.post('')
def create_entry(body: CreateEntry, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    title, name_key = normalized_name(body.title, body.kind)
    data = decode_content(body.content)
    original = decode_content(body.original) if body.original is not None else data
    if body.kind == 'folder' and data:
        raise HTTPException(422, '文件夹不能包含文件内容')
    # Retry the whole allocation transaction on collision, including allocations
    # made by other resource types/processes. The registry PK is the final arbiter.
    for attempt in range(6):
        try:
            parent_folder(session, user, body.parentId)
            existing = session.exec(select(GraphResource).where(GraphResource.owner_id == user.id,
                GraphResource.parent_id == body.parentId, GraphResource.name_key == name_key)).first()
            if existing:
                if body.skipExisting and existing.kind == body.kind:
                    return {**metadata(existing), 'skipped': True}
                raise HTTPException(409, '同名文件或文件夹已存在')
            rid = allocate_resource_id(session, RESOURCE_TYPE_GRAPH, uuid.uuid4().hex)
            entry = GraphResource(id=rid, owner_id=user.id, kind=body.kind, title=title,
                name_key=name_key, parent_id=body.parentId, content=data, original=original,
                revision=1 if data else 0)
            session.add(entry)
            session.commit()
            return {**metadata(entry), 'skipped': False}
        except (IntegrityError, OperationalError):
            session.rollback()
            if attempt == 5:
                raise HTTPException(409, '资源正在更新，请重试')
            time.sleep(.03 * (attempt + 1))


@router.get('/{resource_id}')
def read_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id, 'viewer')
    return {**metadata(entry), 'content': base64.b64encode(entry.content).decode('ascii')}


@router.get('/{resource_id}/original')
def original_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    return Response(owned(session, user, resource_id, 'viewer').original, media_type='application/vnd.project-graph')


@router.put('/{resource_id}/content')
def write_content(resource_id: int, body: WriteContent, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id, 'editor')
    if entry.kind != 'document':
        raise HTTPException(422, '目标不是文件')
    data = decode_content(body.content)
    if not data:
        raise HTTPException(422, '不能用空内容覆盖文件')
    result = session.execute(update(GraphResource).where(GraphResource.id == resource_id,
        GraphResource.deleted == False,
        GraphResource.revision == body.expectedRevision).values(content=data,
        revision=body.expectedRevision + 1, updated_at=time.time()))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, '另一页面已更新此文件，请重新加载后编辑')
    session.commit()
    return metadata(owned(session, user, resource_id, 'editor'))


@router.patch('/{resource_id}')
def change_entry(resource_id: int, body: ChangeEntry, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id)
    if body.parentId is not None:
        parent_folder(session, user, body.parentId)
        cursor = body.parentId
        while cursor:
            if cursor == resource_id:
                raise HTTPException(422, '不能移动到自身或子文件夹')
            cursor = owned(session, user, cursor).parent_id
        entry.parent_id = body.parentId
    if body.title is not None:
        entry.title, entry.name_key = normalized_name(body.title, entry.kind)
    session.add(entry)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, '目标目录存在同名文件')
    return metadata(entry)


@router.delete('/{resource_id}')
def delete_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id)
    if session.exec(select(GraphResource.id).where(GraphResource.parent_id == resource_id, GraphResource.deleted == False)).first():
        raise HTTPException(409, '请先移走文件和子文件夹')
    entry.deleted = True
    entry.name_key = None
    session.add(entry)
    session.commit()
    return {'id': resource_id}
