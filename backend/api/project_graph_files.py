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
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, Query
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from backend.core.access.auth import get_current_active_user
from backend.core.resources.identity import allocate_resource_id, RESOURCE_TYPE_GRAPH
from backend.core.resources.catalog import resource_role, ROLE_RANK
from backend.db import get_session
from backend.models import AppSetting, GraphResource, ResourceAccessGrant, User
from backend.core.collaboration.objects import ObjectHead, read_objects, rooms
from backend.core.project_graph.collaboration import GraphRoom
from backend.core.project_graph.codec import export_prg, has_prg_content

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
    journalDate: date | None = None


class WriteContent(BaseModel):
    content: str
    expectedRevision: int = Field(ge=0)


class AccessUpdate(BaseModel):
    userId: int = Field(ge=1)
    role: Literal['viewer', 'editor', 'deny']


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


def role_of(session: Session, entry: GraphResource, user: User) -> str:
    return resource_role(session, RESOURCE_TYPE_GRAPH, entry.id, entry.owner_id, user)


def metadata(entry: GraphResource, role: str) -> dict:
    # A shared file surfaces at the root so a grantee never learns the owner's folders.
    return dict(id=entry.id, title=entry.title, kind=entry.kind, parentId=entry.parent_id if role == 'manager' else 0,
                journalDate=entry.journal_date, revision=entry.revision, updatedAt=int(entry.updated_at * 1000),
                ownerId=entry.owner_id, role=role)


@router.get('')
def list_entries(session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    # Listing must not load PRG content/original blobs, even for very large files.
    columns = load_only(GraphResource.id, GraphResource.title, GraphResource.kind,
        GraphResource.parent_id, GraphResource.revision, GraphResource.updated_at, GraphResource.owner_id, GraphResource.journal_date)
    rows = session.exec(select(GraphResource).options(columns)
        .where(GraphResource.owner_id == user.id, GraphResource.deleted == False)).all()
    entries = [metadata(row, 'manager') | {'shared': False} for row in rows]
    # Explicit grants only; a deny keeps the file out of the grantee's list entirely.
    shared_ids = {int(value) for value in session.exec(select(ResourceAccessGrant.resource_id).where(
        ResourceAccessGrant.resource_type == RESOURCE_TYPE_GRAPH,
        ResourceAccessGrant.subject_key == f'user:{user.id}',
        ResourceAccessGrant.role.in_(['viewer', 'editor']))).all() if str(value).isdigit()}
    if shared_ids:
        shared_rows = session.exec(select(GraphResource).options(columns).where(
            GraphResource.id.in_(shared_ids), GraphResource.deleted == False,
            GraphResource.kind == 'document',
            GraphResource.owner_id != user.id)).all()
        entries += [metadata(entry, role_of(session, entry, user)) | {'shared': True} for entry in shared_rows]
    entries.sort(key=lambda item: item['title'].casefold())
    collaborative_ids = set(session.exec(select(ObjectHead.resource_id).where(
        ObjectHead.resource_id.in_([item['id'] for item in entries]))).all()) if entries else set()
    for item in entries:
        item['collaborative'] = item['id'] in collaborative_ids
    claim = session.get(AppSetting, 'project-graph.legacy-browser-owner')
    return {'ownerId': user.id, 'legacyBrowserImport': bool(claim and claim.value.get('owner_id') == user.id), 'entries': entries}


@router.post('/journals/{day}')
def open_journal(day: date, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    """Look up a daily canvas without allocating a file for an empty day."""
    entry = session.exec(select(GraphResource).where(GraphResource.owner_id == user.id,
        GraphResource.journal_date == day.isoformat(), GraphResource.deleted == False)).first()
    return metadata(entry, 'manager') if entry else None


@router.post('/journals/{day}/content')
def create_journal(day: date, body: WriteContent, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    """Persist the first authored content atomically; empty initialization stays virtual.

    Uses an explicit calendar date supplied by the client, never server timezone or
    filenames. Renaming/moving does not affect identity. Existing ordinary files
    with the date as their title are preserved.
    """
    data = decode_content(body.content)
    if body.expectedRevision != 0:
        raise HTTPException(409, '请重新打开记录后保存')
    if not data or not has_prg_content(data):
        return None
    value = day.isoformat()
    for attempt in range(6):
        try:
            existing = session.exec(select(GraphResource).where(GraphResource.owner_id == user.id,
                GraphResource.journal_date == value, GraphResource.deleted == False)).first()
            if existing:
                if existing.content == data:
                    return metadata(existing, 'manager')
                raise HTTPException(409, '另一页面已创建当天记录，请下载当前内容后重新打开，避免覆盖')
            rid = allocate_resource_id(session, RESOURCE_TYPE_GRAPH, uuid.uuid4().hex)
            title, key = normalized_name(value, 'document')
            if session.exec(select(GraphResource.id).where(GraphResource.owner_id == user.id,
                    GraphResource.parent_id == 0, GraphResource.name_key == key)).first():
                title, key = normalized_name(f'{value} · 每日记录 {rid}', 'document')
            entry = GraphResource(id=rid, owner_id=user.id, kind='document', title=title,
                name_key=key, journal_date=value, content=data, original=data, revision=1)
            session.add(entry)
            session.commit()
            return metadata(entry, 'manager')
        except (IntegrityError, OperationalError):
            session.rollback()
            if attempt == 5:
                raise HTTPException(409, '每日记录正在更新，请重试')
            time.sleep(.03 * (attempt + 1))


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
                    return {**metadata(existing, 'manager'), 'skipped': True}
                raise HTTPException(409, '同名文件或文件夹已存在')
            rid = allocate_resource_id(session, RESOURCE_TYPE_GRAPH, uuid.uuid4().hex)
            entry = GraphResource(id=rid, owner_id=user.id, kind=body.kind, title=title,
                name_key=name_key, parent_id=body.parentId, content=data, original=original,
                revision=1 if data else 0)
            session.add(entry)
            session.commit()
            return {**metadata(entry, 'manager'), 'skipped': False}
        except (IntegrityError, OperationalError):
            session.rollback()
            if attempt == 5:
                raise HTTPException(409, '资源正在更新，请重试')
            time.sleep(.03 * (attempt + 1))


@router.get('/{resource_id}')
async def read_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    room = rooms.setdefault(resource_id, GraphRoom(resource_id))
    async with room.guard:
        entry = owned(session, user, resource_id, 'viewer')
        enabled = session.get(ObjectHead, resource_id) is not None
        content = export_prg(entry.content, read_objects(session, resource_id)) if enabled else entry.content
        return {**metadata(entry, role_of(session, entry, user)), 'collaborative': enabled,
                'content': base64.b64encode(content).decode('ascii')}


@router.get('/{resource_id}/original')
def original_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    return Response(owned(session, user, resource_id, 'viewer').original, media_type='application/vnd.project-graph')


def access_payload(session: Session, entry: GraphResource) -> dict:
    owner = session.get(User, entry.owner_id)
    grants = session.exec(select(ResourceAccessGrant).where(
        ResourceAccessGrant.resource_type == RESOURCE_TYPE_GRAPH,
        ResourceAccessGrant.resource_id == str(entry.id),
        ResourceAccessGrant.subject_type == 'user').order_by(ResourceAccessGrant.created_at)).all()
    items = []
    for grant in grants:
        user_id = grant.subject_user_id
        if user_id is None and grant.subject_key.startswith('user:'):
            try:
                user_id = int(grant.subject_key.split(':', 1)[1])
            except ValueError:
                continue
        if user_id is None:
            continue
        target = session.get(User, user_id)
        items.append({'userId': user_id, 'username': target.username if target else '',
                      'nickname': target.nickname if target else '', 'role': grant.role})
    return {'owner': {'id': entry.owner_id, 'username': owner.username if owner else '',
                      'nickname': owner.nickname if owner else ''}, 'grants': items}


def shareable(session: Session, user: User, resource_id: int) -> GraphResource:
    entry = owned(session, user, resource_id)
    if entry.owner_id != user.id:
        raise HTTPException(404, '文件不存在')
    if entry.kind != 'document':
        raise HTTPException(422, '文件夹不支持分享')
    return entry


@router.get('/{resource_id}/access')
def get_access(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    return access_payload(session, shareable(session, user, resource_id))


@router.put('/{resource_id}/access')
def update_access(resource_id: int, body: AccessUpdate, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = shareable(session, user, resource_id)
    if body.userId == entry.owner_id:
        raise HTTPException(422, '不能修改自己的权限')
    target = session.get(User, body.userId)
    if not target:
        raise HTTPException(404, '用户不存在')
    if body.role != 'deny' and not target.is_active:
        raise HTTPException(422, '目标用户不可用')
    subject_key = f'user:{body.userId}'
    for attempt in range(4):
        grant = session.exec(select(ResourceAccessGrant).where(
            ResourceAccessGrant.resource_type == RESOURCE_TYPE_GRAPH,
            ResourceAccessGrant.resource_id == str(resource_id),
            ResourceAccessGrant.subject_key == subject_key)).first()
        if grant:
            grant.role, grant.subject_type, grant.subject_user_id = body.role, 'user', body.userId
            grant.updated_at, grant.updated_by_user_id = time.time(), user.id
        else:
            grant = ResourceAccessGrant(resource_type=RESOURCE_TYPE_GRAPH, resource_id=str(resource_id),
                subject_key=subject_key, subject_type='user', subject_user_id=body.userId,
                role=body.role, updated_by_user_id=user.id)
        session.add(grant)
        try:
            session.commit()
            break
        except IntegrityError:
            session.rollback()
            if attempt == 3:
                raise HTTPException(409, '权限正在更新，请重试')
            time.sleep(.03 * (attempt + 1))
    return access_payload(session, entry)


@router.put('/{resource_id}/content')
def write_content(resource_id: int, body: WriteContent, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id, 'editor')
    if entry.kind != 'document':
        raise HTTPException(422, '目标不是文件')
    role = role_of(session, entry, user)
    data = decode_content(body.content)
    if not data:
        raise HTTPException(422, '不能用空内容覆盖文件')
    result = session.execute(update(GraphResource).where(GraphResource.id == resource_id,
        GraphResource.deleted == False,
        ~select(ObjectHead).where(ObjectHead.resource_id == resource_id).exists(),
        GraphResource.revision == body.expectedRevision).values(content=data,
        revision=body.expectedRevision + 1, updated_at=time.time()))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, '另一页面已更新此文件，请重新加载后编辑')
    session.commit()
    return metadata(owned(session, user, resource_id, 'editor'), role)


@router.patch('/{resource_id}')
def change_entry(resource_id: int, body: ChangeEntry, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    entry = owned(session, user, resource_id)
    if 'journalDate' in body.model_fields_set:
        if entry.kind != 'document':
            raise HTTPException(422, '只有图文档可以设置记录日期')
        entry.journal_date = body.journalDate.isoformat() if body.journalDate else None
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
        raise HTTPException(409, '目标目录存在同名文件，或该日期已有每日记录')
    return metadata(entry, role_of(session, entry, user))


@router.delete('/{resource_id}')
def delete_entry(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user),
                 expectedRevision: int | None = Query(default=None, ge=0), onlyIfEmpty: bool = False):
    entry = owned(session, user, resource_id)
    if session.exec(select(GraphResource.id).where(GraphResource.parent_id == resource_id, GraphResource.deleted == False)).first():
        raise HTTPException(409, '请先移走文件和子文件夹')
    if expectedRevision is not None and entry.revision != expectedRevision:
        raise HTTPException(409, '文件已更新，请刷新后重试')
    if onlyIfEmpty:
        if not entry.journal_date or session.get(ObjectHead, resource_id) is not None:
            raise HTTPException(409, '仅可清理非协作的每日空白记录')
        if entry.content and has_prg_content(entry.content):
            raise HTTPException(409, '记录已有内容，已保留')
    result = session.execute(update(GraphResource).where(GraphResource.id == resource_id,
        GraphResource.revision == entry.revision, GraphResource.deleted == False,
        ~select(ObjectHead).where(ObjectHead.resource_id == resource_id).exists() if onlyIfEmpty else True
    ).values(deleted=True, journal_date=None, name_key=None))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, '文件已更新，请刷新后重试')
    session.commit()
    return {'id': resource_id}


from backend.api.project_graph_collaboration import router as collaboration_router
router.include_router(collaboration_router)
