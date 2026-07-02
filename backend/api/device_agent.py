from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from backend.core.access.auth import verify_api_token
from backend.core.device_agent import (
    DeviceAgentError,
    append_device_agent_turn,
    create_device_agent_session,
    get_device_agent_config,
    get_device_agent_manifest,
    get_device_agent_session,
    get_device_agent_turn,
    list_device_agent_sessions,
    save_device_agent_config,
)
from backend.db import get_session


router = APIRouter(dependencies=[Depends(verify_api_token)])


class DeviceAgentConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    display_name: Optional[str] = None
    device_role: Optional[str] = None
    local_context: Optional[str] = None
    responsibilities: Optional[str] = None
    default_provider: Optional[str] = None
    default_model: Optional[str] = None


class DeviceAgentRequester(BaseModel):
    kind: Literal["device", "user", "system"] = "device"
    id: str = ""
    display_name: str = ""


class DeviceAgentTurnRequest(BaseModel):
    requester: DeviceAgentRequester = Field(default_factory=DeviceAgentRequester)
    request_type: Literal["ask", "diagnose", "delegate", "repair"] = "ask"
    instruction: str
    context: dict[str, Any] = Field(default_factory=dict)


class DeviceAgentSessionCreateRequest(DeviceAgentTurnRequest):
    title: Optional[str] = None


@router.get("/config")
def get_config(session: Session = Depends(get_session)):
    return get_device_agent_config(session)


@router.put("/config")
def put_config(payload: DeviceAgentConfigRequest, session: Session = Depends(get_session)):
    return save_device_agent_config(session, payload.model_dump(exclude_unset=True))


@router.get("/manifest")
def get_manifest(session: Session = Depends(get_session)):
    return get_device_agent_manifest(session)


@router.get("/sessions")
def list_sessions(
    limit: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return {"items": list_device_agent_sessions(session, limit=limit)}


@router.post("/sessions")
def create_session(payload: DeviceAgentSessionCreateRequest, session: Session = Depends(get_session)):
    try:
        return create_device_agent_session(
            session,
            requester=payload.requester.model_dump(),
            request_type=payload.request_type,
            instruction=payload.instruction,
            context=payload.context,
            title=payload.title,
        )
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/sessions/{session_id}")
def get_session_detail(session_id: str, session: Session = Depends(get_session)):
    try:
        return get_device_agent_session(session, session_id)
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/turns")
def append_turn(session_id: str, payload: DeviceAgentTurnRequest, session: Session = Depends(get_session)):
    try:
        return append_device_agent_turn(
            session,
            session_id,
            requester=payload.requester.model_dump(),
            request_type=payload.request_type,
            instruction=payload.instruction,
            context=payload.context,
        )
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/turns/{turn_id}")
def get_turn(turn_id: str, session: Session = Depends(get_session)):
    try:
        return get_device_agent_turn(session, turn_id)
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
