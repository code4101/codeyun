"""角色档案与服务器关系 HTTP 适配；功能访问控制由父路由统一组合。"""
from fastapi import APIRouter
from fastapi import (
    Depends,
    HTTPException,
    Query,
)
from backend.core.fanxiu.catalog.status_models import (
    FanxiuPlayerProfileRecordListResponse,
    FanxiuServerRelationTreeResponse,
    FanxiuServerRelationTreeUpdateRequest,
)
from sqlmodel import (
    Session,
)
from backend.models import (
    User,
)
from backend.core.access.auth import (
    get_current_active_user,
)
from backend.db import (
    get_session,
)
from backend.core.fanxiu.player_profiles import (
    list_daily_fanxiu_player_profile_records,
    list_daily_fanxiu_player_xianlv_team_records,
    list_fanxiu_player_profile_records,
    list_latest_fanxiu_player_profile_records,
    list_latest_fanxiu_player_xianlv_team_records,
)
from backend.core.fanxiu.catalog.server_relations import (
    load_fanxiu_server_relations,
    save_fanxiu_server_relations,
)

status_router = APIRouter()

@status_router.get("/business-data/player-profiles", response_model=FanxiuPlayerProfileRecordListResponse)
def list_fanxiu_business_player_profiles(
    limit: int = Query(1000, ge=1, le=5000),
    history: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    if history:
        records = list_fanxiu_player_profile_records(session, limit=limit)
    else:
        records = list_latest_fanxiu_player_profile_records(session, limit=limit)
    daily_records = list_daily_fanxiu_player_profile_records(session, limit=limit)
    xianlv_team_records = list_latest_fanxiu_player_xianlv_team_records(session, limit=limit)
    xianlv_team_daily_records = list_daily_fanxiu_player_xianlv_team_records(session, limit=limit)
    return FanxiuPlayerProfileRecordListResponse(
        ok=True,
        count=len(records),
        records=records,
        daily_count=len(daily_records),
        daily_records=daily_records,
        xianlv_team_count=len(xianlv_team_records),
        xianlv_team_records=xianlv_team_records,
        xianlv_team_daily_count=len(xianlv_team_daily_records),
        xianlv_team_daily_records=xianlv_team_daily_records,
    )

@status_router.get("/server-relations", response_model=FanxiuServerRelationTreeResponse)
def get_fanxiu_server_relations() -> FanxiuServerRelationTreeResponse:
    return FanxiuServerRelationTreeResponse(**load_fanxiu_server_relations())

@status_router.put("/server-relations", response_model=FanxiuServerRelationTreeResponse)
def update_fanxiu_server_relations(
    payload: FanxiuServerRelationTreeUpdateRequest,
) -> FanxiuServerRelationTreeResponse:
    try:
        saved = save_fanxiu_server_relations(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FanxiuServerRelationTreeResponse(**saved)
