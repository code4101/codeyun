"""用户阅读工作区：标签操作在服务端合并，单书阅读进度仍由阅读器接口保存。"""
from __future__ import annotations

import threading
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user, get_optional_current_user_from_token
from backend.db import get_session
from backend.models import AppSetting, User, ResourceIdentity, LibraryBookAsset

from backend.core.resources.identity import allocate_resource_id, RESOURCE_TYPE_EBOOK, RESOURCE_TYPE_SKILL_BOOK

router = APIRouter()
_mutation_lock = threading.RLock()


class ReaderTab(BaseModel):
    kind: Literal['pdf', 'ebook', 'skill']
    id: str = Field(min_length=1, max_length=256)
    publicId: int | None = Field(default=None, gt=0)
    title: str = Field(default='', max_length=512)
    bookshelfId: str = Field(default='', max_length=256)
    pageSize: int = Field(default=1600, ge=200, le=20000)
    readingMode: Literal['scroll', 'paginated'] = 'scroll'

    @property
    def key(self) -> str:
        return f'{self.kind}:{self.id}'


class ReaderWorkspace(BaseModel):
    tabs: list[ReaderTab] = Field(default_factory=list)
    active: str = ''
    layout: dict = Field(default_factory=dict)
    revision: int = 0


class WorkspaceCommand(BaseModel):
    action: Literal['open', 'close', 'activate', 'move', 'layout', 'title']
    tab: ReaderTab | None = None
    key: str = Field(default='', max_length=270)
    before: str = Field(default='', max_length=270)
    layout: dict = Field(default_factory=dict)
    title: str = Field(default='', max_length=512)


def apply_workspace_command(state: ReaderWorkspace, command: WorkspaceCommand) -> ReaderWorkspace:
    """只修改指定标签；重复打开激活已有标签，关闭后优先激活右邻居。"""
    state = state.model_copy(deep=True)
    keys = [tab.key for tab in state.tabs]
    if command.action == 'open' and command.tab:
        tab = command.tab
        if tab.key in keys:
            index = keys.index(tab.key)
            # 再打开只激活；保留原来的阅读模式和书架上下文。
            if tab.title:
                state.tabs[index].title = tab.title
        else:
            state.tabs.append(tab)
        state.active = tab.key
    elif command.action == 'close' and command.key in keys:
        index = keys.index(command.key)
        state.tabs.pop(index)
        if state.active == command.key:
            state.active = state.tabs[min(index, len(state.tabs) - 1)].key if state.tabs else ''
    elif command.action == 'activate' and command.key in keys:
        state.active = command.key
    elif command.action == 'title' and command.key in keys:
        state.tabs[keys.index(command.key)].title = command.title
    elif command.action == 'move' and command.key in keys and command.key != command.before:
        tab = state.tabs.pop(keys.index(command.key))
        remaining = [item.key for item in state.tabs]
        state.tabs.insert(remaining.index(command.before) if command.before in remaining else len(state.tabs), tab)
    elif command.action == 'layout':
        state.layout = command.layout
    state.revision += 1
    return state


def number_tabs(session: Session, state: ReaderWorkspace) -> ReaderWorkspace:
    """公开地址复用全局资源编号，内部资源键仍用于内容和阅读进度。"""
    for tab in state.tabs:
        if tab.kind == 'pdf':
            tab.publicId = int(tab.id)
        else:
            resource_type = RESOURCE_TYPE_EBOOK if tab.kind == 'ebook' else RESOURCE_TYPE_SKILL_BOOK
            key = tab.id
            tab.publicId = allocate_resource_id(session, resource_type, key)
            session.flush()
    return state


@router.get('/resources/{resource_id}', response_model=ReaderTab)
def resolve_reader_resource(resource_id: int, session: Session = Depends(get_session),
                            user: User | None = Depends(get_optional_current_user_from_token)):
    """编号决定阅读器类型；内容权限仍由资源所属模块检查。"""
    identity = session.get(ResourceIdentity, resource_id)
    if identity is None:
        raise HTTPException(404, '阅读资源不存在')
    if identity.resource_type == 'pdf':
        from backend.api.pdf_documents import get_pdf_document
        document = get_pdf_document(resource_id, session, user)
        return ReaderTab(kind='pdf', id=str(resource_id), publicId=resource_id, title=document.title)
    if identity.resource_type == RESOURCE_TYPE_EBOOK:
        asset = session.get(LibraryBookAsset, identity.legacy_pk)
        if user is not None and asset is not None and asset.owner_user_id == user.id:
            return ReaderTab(kind='ebook', id=asset.id, publicId=resource_id, title=asset.title)
    if identity.resource_type == RESOURCE_TYPE_SKILL_BOOK and user is not None and identity.legacy_pk == 'local-skill':
        return ReaderTab(kind='skill', id='local-skill', publicId=resource_id)
    raise HTTPException(404, '阅读资源不存在')


@router.get('', response_model=ReaderWorkspace)
def read_workspace(session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    with _mutation_lock:
        row = session.get(AppSetting, f'user:{user.id}:reader_workspace')
        state = number_tabs(session, ReaderWorkspace.model_validate(row.value) if row else ReaderWorkspace())
        session.commit()
        return state


@router.post('/commands', response_model=ReaderWorkspace)
def update_workspace(command: WorkspaceCommand, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    # CodeYun 是本机单实例；串行读改写避免两个页面同时打开新书时丢失标签。
    with _mutation_lock:
        key = f'user:{user.id}:reader_workspace'
        row = session.get(AppSetting, key)
        state = ReaderWorkspace.model_validate(row.value) if row else ReaderWorkspace()
        state = number_tabs(session, apply_workspace_command(state, command))
        if row is None:
            row = AppSetting(key=key)
        row.value = state.model_dump()
        row.updated_at = time.time()
        session.add(row)
        session.commit()
        return state
