"""Owner-only native window tools, independent of Codex sessions."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from backend.core.access.auth import get_current_user_from_token
from backend.models import User
from backend.core.devices import window_projection as projection

router = APIRouter()


def require_owner(user: User = Depends(get_current_user_from_token)):
    if not user.is_superuser:
        raise HTTPException(403, '本机窗口操作仅对管理员开放')
    return user


class PointerPoint(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class WindowInput(BaseModel):
    window_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=256)
    action: Literal['click', 'double_click', 'drag', 'scroll', 'text', 'undo']
    button: Literal['left', 'right', 'middle'] = 'left'
    path: list[PointerPoint] = Field(default_factory=list, max_length=96)
    duration_ms: int = Field(default=600, ge=0, le=2000)
    axis: Literal['vertical', 'horizontal'] = 'vertical'
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.5, ge=0, le=1)
    delta: int = Field(default=0, ge=-1200, le=1200)
    text: str = Field(default='', max_length=100000)
    send: bool = False
    send_key: Literal['enter', 'ctrl_enter'] = 'enter'

    @model_validator(mode='after')
    def require_drag_path(self):
        if self.action == 'drag' and len(self.path) < 2:
            raise ValueError('拖拽至少需要起点和终点')
        return self


class WindowSelection(BaseModel):
    window_id: str = Field(min_length=1, max_length=128)


@router.post('/activate')
def activate(payload: WindowSelection, _: User = Depends(require_owner)):
    try:
        return projection.activate_projection_window(payload.window_id)
    except Exception as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
def windows(_: User = Depends(require_owner)):
    return {'windows': projection.list_projection_windows()}


@router.get('/frame')
def frame(window_id: str, token: str | None = None, _: User = Depends(require_owner)):
    try:
        return projection.projection_frame(token, window_id)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post('/input')
def window_input(payload: WindowInput, _: User = Depends(require_owner)):
    try:
        return projection.projection_input(**payload.model_dump())
    except Exception as exc:
        # Never retry a GUI mutation: a failed response may follow an accepted paste.
        raise HTTPException(409, f'操作未确认，请刷新画面核对，不要重复发送：{exc}') from exc
