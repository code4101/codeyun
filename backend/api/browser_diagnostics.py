"""诊断公共契约：管理员限时开关/读结果，登录用户上报有限的结构化状态。"""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from backend.core.access.auth import get_current_active_superuser, get_current_active_user
from backend.core.runtime.browser_diagnostics import diagnostics
from backend.models import User

router = APIRouter()


class DiagnosticControl(BaseModel):
    enabled: bool
    ttl_seconds: int = Field(default=600, ge=60, le=1800)
    user_agent_contains: list[str] = Field(default_factory=list, max_length=10)


class DiagnosticEvent(BaseModel):
    kind: Literal['snapshot', 'resize', 'error', 'rejection', 'request']
    path: str = Field(default='', max_length=500)
    detail: str = Field(default='', max_length=1000)
    values: dict[str, float | bool | None] = Field(default_factory=dict, max_length=40)


class DiagnosticReport(BaseModel):
    run_id: str = Field(min_length=1, max_length=40)
    events: list[DiagnosticEvent] = Field(min_length=1, max_length=10)


@router.get('', dependencies=[Depends(get_current_active_superuser)])
def get_diagnostics():
    return diagnostics.snapshot()


@router.put('/control', dependencies=[Depends(get_current_active_superuser)])
def control_diagnostics(payload: DiagnosticControl):
    markers = [marker.strip().lower() for marker in payload.user_agent_contains]
    if any(not marker or len(marker) > 100 for marker in markers):
        raise HTTPException(422, '浏览器标识须为 1 至 100 个字符')
    return diagnostics.configure(payload.enabled, payload.ttl_seconds, markers)


@router.post('/events')
def report_diagnostics(payload: DiagnosticReport, user: User = Depends(get_current_active_user)):
    if not diagnostics.report(payload.run_id, user.id, [event.model_dump() for event in payload.events]):
        raise HTTPException(410, '诊断已关闭、过期或达到采集上限')
    return {'accepted': True}
