"""Shared game surface: login + explicit account whitelist, no device tokens."""
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from PIL import Image
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user, get_current_active_superuser
from backend.core.fanxiu.game.sharing import (
    grant_game_sharing, require_game_sharing, shared_game_status, execute_shared_game_input,
)
from backend.db import get_session
from backend.models import User

router = APIRouter(prefix='/api/fanxiu/shared-game',tags=['fanxiu-game-sharing'])


class GrantRequest(BaseModel):
    username: str = Field(min_length=1,max_length=120)
    view: bool = True
    control: bool = True


class InputRequest(BaseModel):
    request_id: UUID
    x: float = Field(ge=0,lt=1)
    y: float = Field(ge=0,lt=1)
    end_x: float | None = Field(default=None,ge=0,lt=1)
    end_y: float | None = Field(default=None,ge=0,lt=1)
    width: int = Field(ge=100,le=4096)
    height: int = Field(ge=100,le=4096)

    @model_validator(mode='after')
    def require_both_drag_coordinates(self):
        if (self.end_x is None) != (self.end_y is None):
            raise ValueError('拖拽须同时提供终点横纵坐标')
        return self


@router.put('/grants')
def grant(req: GrantRequest, user: User = Depends(get_current_active_superuser), session: Session = Depends(get_session)):
    try:
        return grant_game_sharing(session,**req.model_dump())
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc


@router.get('/status')
def status(user: User = Depends(get_current_active_user)):
    return shared_game_status(user)


@router.get('/frame')
def frame(user: User = Depends(get_current_active_user)):
    require_game_sharing(user)
    from backend.core.fanxiu.client.mumu_control import screencap_mumu_adb_cached_png
    data,_ = screencap_mumu_adb_cached_png(max_age_seconds=1)
    return Response(data,media_type='image/png',headers={'Cache-Control':'no-store'})


@router.post('/input')
def input_action(req: InputRequest, user: User = Depends(get_current_active_user)):
    require_game_sharing(user,control=True)
    from backend.core.fanxiu.client.mumu_control import screencap_mumu_adb_cached_png
    data,_ = screencap_mumu_adb_cached_png(max_age_seconds=1)
    with Image.open(io.BytesIO(data)) as image:
        if image.size != (req.width,req.height):
            raise HTTPException(409,'游戏画面尺寸已变化，请刷新后再操作')
    args = {'frame_width':req.width,'frame_height':req.height,'fixed_width':0,'fixed_height':0,
            'rotate':'none','crop':f'0,0,{req.width},{req.height}','trim_border':'0,0,0,0','input_backend':'adb'}
    if req.end_x is not None and req.end_y is not None:
        args.update(start_x=req.x*req.width,start_y=req.y*req.height,
                    end_x=req.end_x*req.width,end_y=req.end_y*req.height,duration_ms=350)
        kind='drag'
    else:
        args.update(x=req.x*req.width,y=req.y*req.height)
        kind='click'
    return execute_shared_game_input(user,{'kind':kind,'args':args},request_id=str(req.request_id))
