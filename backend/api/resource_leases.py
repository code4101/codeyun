"""Business-independent resource lease authority shared with workers and CLI."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.access.auth import get_current_active_superuser
from backend.core.runtime.resource_leases import ResourceLeaseStore, LeaseError

router = APIRouter(dependencies=[Depends(get_current_active_superuser)])


class AcquireLease(BaseModel):
    resource: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=200)


class LeaseCredentials(BaseModel):
    resource: str = Field(min_length=1, max_length=200)
    token: str = Field(min_length=1, max_length=100)


def call_lease(action: str, *args):
    try:
        return getattr(ResourceLeaseStore(), action)(*args)
    except LeaseError as exc:
        raise HTTPException(status_code=409, detail={'code': exc.code}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get('/status')
def status(resource: str = Query(min_length=1, max_length=200)):
    return call_lease('status', resource)


@router.post('/acquire')
def acquire(body: AcquireLease):
    return call_lease('acquire', body.resource, body.owner)


@router.post('/check')
def check(body: LeaseCredentials):
    return call_lease('check', body.resource, body.token)


@router.post('/renew')
def renew(body: LeaseCredentials):
    return call_lease('renew', body.resource, body.token)


@router.post('/release')
def release(body: LeaseCredentials):
    return {'released': call_lease('release', body.resource, body.token)}
