"""Unified resource metadata for explorers; content stays behind provider APIs."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session
from backend.core.access.auth import get_current_active_user
from backend.core.resources.catalog import list_owned_resources, resolve_resource
from backend.db import get_session
from backend.models import User

router = APIRouter()


@router.get('')
def resources(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
              session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    rows = list_owned_resources(session, user.id, after=after, limit=limit)
    return {'items': rows, 'next': rows[-1]['id'] if len(rows) == limit else None}


@router.get('/{resource_id}')
def resource(resource_id: int, session: Session = Depends(get_session), user: User = Depends(get_current_active_user)):
    result = resolve_resource(session, resource_id, user)
    if result is None:
        raise HTTPException(404, '资源不存在')
    return result
