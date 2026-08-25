from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user
from backend.core.access.feature_access_guard import ensure_feature_access
from backend.core.fanxiu.instrumentation import (
    DEFAULT_FANXIU_PACKAGE_NAME,
    FanxiuInstrumentationError,
    fanxiu_instrumentation_service,
)
from backend.core.fanxiu.instrumentation.policy import (
    FanxiuInstrumentationPolicyError,
    instrumentation_policy_snapshot,
)
from backend.core.fanxiu.beast_spirit_default_layout import (
    read_beast_spirit_default_layout,
)
from backend.db import get_session
from backend.models import User


router = APIRouter()


class FanxiuInstrumentationTarget(BaseModel):
    device_id: str = Field(default="", max_length=128)
    package_name: str = Field(
        default=DEFAULT_FANXIU_PACKAGE_NAME,
        min_length=1,
        max_length=255,
    )


class FanxiuInstrumentationProbeRequest(FanxiuInstrumentationTarget):
    module_names: list[str] = Field(
        default_factory=lambda: [
            "libil2cpp.so",
            "libunity.so",
            "libtolua.so",
            "libc.so",
        ],
        max_length=64,
    )
    ensure_server: bool = True
    timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


def _authorize(current_user: User, session: Session) -> None:
    ensure_feature_access(
        session,
        feature_key="fanxiu",
        current_user=current_user,
    )


def _as_http_error(
    exc: FanxiuInstrumentationError | FanxiuInstrumentationPolicyError,
) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/dynamic-instrumentation/status")
def get_fanxiu_instrumentation_status(
    device_id: str = "",
    package_name: str = DEFAULT_FANXIU_PACKAGE_NAME,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    try:
        return fanxiu_instrumentation_service.inspect(
            device_id=device_id,
            package_name=package_name,
        )
    except FanxiuInstrumentationError as exc:
        raise _as_http_error(exc) from exc


@router.get("/dynamic-instrumentation/capabilities")
def get_fanxiu_instrumentation_capabilities(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return {
        "policy": instrumentation_policy_snapshot(),
        "capabilities": fanxiu_instrumentation_service.capabilities(),
    }


@router.get("/dynamic-instrumentation/lingquan/question")
def get_fanxiu_lingquan_question(
    max_age_seconds: float = 2.0,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.lingquan_question_snapshot(
        max_age_seconds=max(0.1, min(float(max_age_seconds), 10.0)),
    )


@router.get("/dynamic-instrumentation/dongtian")
def get_fanxiu_dongtian_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.dongtian_snapshot()


@router.get("/dynamic-instrumentation/lingmai")
def get_fanxiu_lingmai_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.lingmai_snapshot()


@router.get("/dynamic-instrumentation/mail")
def get_fanxiu_mail_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.mail_snapshot()


@router.get("/dynamic-instrumentation/beast-spirit")
def get_fanxiu_beast_spirit_snapshot(
    optimize: bool = True,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.beast_spirit_snapshot(optimize=optimize)


@router.get("/beast-spirit/default-layout")
def get_fanxiu_beast_spirit_default_layout(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    layout = read_beast_spirit_default_layout(session)
    if layout is None:
        raise HTTPException(status_code=404, detail="兽魂默认布局尚未生成")
    return layout


@router.get("/dynamic-instrumentation/lilian-event/catalog")
def get_fanxiu_lilian_event_catalog_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.lilian_event_catalog_snapshot()


@router.get("/dynamic-instrumentation/daofa")
def get_fanxiu_daofa_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.daofa_snapshot()


@router.get("/dynamic-instrumentation/xianyuan-duel")
def get_fanxiu_xianyuan_duel_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.xianyuan_duel_snapshot()


@router.get("/dynamic-instrumentation/activity-ranks/lingzhuang-huadao")
def get_fanxiu_lingzhuang_huadao_ranking_snapshot(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.lingzhuang_huadao_ranking_snapshot()


@router.get("/dynamic-instrumentation/activity-ranks/{activity_id}")
def get_fanxiu_activity_rank_snapshot(
    activity_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.activity_rank_snapshot(activity_id)


@router.get("/dynamic-instrumentation/red-packet/pending")
def get_fanxiu_red_packet_pending(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    return fanxiu_instrumentation_service.red_packet_pending()


@router.post("/dynamic-instrumentation/server/ensure")
def ensure_fanxiu_instrumentation_server(
    request: FanxiuInstrumentationTarget,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    try:
        return fanxiu_instrumentation_service.ensure_server(
            device_id=request.device_id,
            package_name=request.package_name,
        )
    except (FanxiuInstrumentationError, FanxiuInstrumentationPolicyError) as exc:
        raise _as_http_error(exc) from exc


@router.post("/dynamic-instrumentation/probe")
def probe_fanxiu_instrumentation(
    request: FanxiuInstrumentationProbeRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    _authorize(current_user, session)
    try:
        return fanxiu_instrumentation_service.probe(
            device_id=request.device_id,
            package_name=request.package_name,
            module_names=request.module_names,
            ensure_server=request.ensure_server,
            timeout=request.timeout_seconds,
        )
    except (FanxiuInstrumentationError, FanxiuInstrumentationPolicyError) as exc:
        raise _as_http_error(exc) from exc
