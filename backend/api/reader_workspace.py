"""阅读资源编号与权限解析；标签和布局由浏览器本地保存。"""
from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_optional_current_user_from_token
from backend.db import get_session
from backend.models import User, ResourceIdentity, LibraryBookAsset

from backend.core.resources.identity import allocate_resource_id, RESOURCE_TYPE_EBOOK, RESOURCE_TYPE_SKILL_BOOK

router = APIRouter()
_identity_lock = threading.RLock()


class ReaderResourceReference(BaseModel):
    kind: Literal['pdf', 'ebook', 'skill']
    id: str = Field(min_length=1, max_length=256)


class ReaderTab(ReaderResourceReference):
    publicId: int | None = Field(default=None, gt=0)
    title: str = Field(default='', max_length=512)
    bookshelfId: str = Field(default='', max_length=256)
    pageSize: int = Field(default=1600, ge=200, le=20000)
    readingMode: Literal['scroll', 'paginated'] = 'scroll'

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


@router.post('/resources', response_model=ReaderTab)
def identify_reader_resource(tab: ReaderResourceReference, session: Session = Depends(get_session),
                             user: User | None = Depends(get_optional_current_user_from_token)):
    """为可访问资源取得稳定编号，不接收或保存工作台现场。"""
    if tab.kind == 'pdf':
        if not tab.id.isdecimal():
            raise HTTPException(404, '阅读资源不存在')
        return resolve_reader_resource(int(tab.id), session, user)
    if user is None:
        raise HTTPException(401, '请先登录')
    if tab.kind == 'ebook':
        asset = session.get(LibraryBookAsset, tab.id)
        if asset is None or asset.owner_user_id != user.id:
            raise HTTPException(404, '阅读资源不存在')
        resource_type = RESOURCE_TYPE_EBOOK
    else:
        if tab.id != 'local-skill':
            raise HTTPException(404, '阅读资源不存在')
        resource_type = RESOURCE_TYPE_SKILL_BOOK
    # 单实例内串行分配全局编号，多个浏览器同时首次打开资源时保持唯一。
    with _identity_lock:
        number = allocate_resource_id(session, resource_type, tab.id)
        session.commit()
    return resolve_reader_resource(number, session, user)
