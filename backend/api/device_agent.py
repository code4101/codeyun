from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from backend.core.device_agent.artifacts import resolve_attachment
from backend.core.device_agent.service import DeviceAgentBusyError
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


@router.get("/protocol")
def get_protocol():
    """设备接入契约；两端仅需 HTTP 和设备 token，不依赖模型 SDK。"""
    return {
        "version": "v1", "base_path": "/api/device-agent",
        "authentication": {"header": "X-Device-Token", "alternative": "Authorization: Bearer <device-token>",
                           "obtain": "由本机所有者从设备管理取得 token，单独提供给可信对端"},
        "routes": {
            "manifest": "GET /manifest", "request": "POST /sessions",
            "session": "GET /sessions/{session_id}",
            "followup": "POST /sessions/{session_id}/turns",
            "result": "GET /turns/{turn_id}",
            "attachment": "GET /turns/{turn_id}/attachments/{attachment_id}",
        },
        "request_example": {"requester": {"kind": "device", "id": "peer-device", "display_name": "对端"},
                            "request_type": "ask", "instruction": "查询本机系统状态并交付报告文件", "context": {}},
        "flow": ["POST /sessions 返回 id 和 last_turn_id，表示已受理",
                 "每两秒 GET /turns/{last_turn_id}，pending/running 为执行中",
                 "completed 后读取 result_report；failed 后读取 error_message，不自动重发有副作用的请求",
                 "按 result_report.attachments[].download_path 下载文件，沿用同一鉴权头并核验 sha256",
                 "同会话追问使用 POST /sessions/{id}/turns，返回的 id 是新 turn_id"],
        "errors": {"401/403": "鉴权失败", "400": "请求无效或代理禁用", "404": "会话/轮次/附件不存在",
                   "409": "同会话仍在执行，等待当前轮次终止后再追问"},
        "limits": {"attachments_per_turn": 10, "attachment_max_bytes": 32 * 1024 * 1024,
                   "interrupt": False, "automatic_callback": False,
                   "retry": "POST 超时后先查询会话列表确认已受理情况，避免重复执行"},
    }


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
    except DeviceAgentBusyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/turns/{turn_id}")
def get_turn(turn_id: str, session: Session = Depends(get_session)):
    try:
        return get_device_agent_turn(session, turn_id)
    except DeviceAgentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/turns/{turn_id}/attachments/{attachment_id}")
def download_attachment(turn_id: str, attachment_id: str, session: Session = Depends(get_session)):
    """下载已完成轮次实际交付的文件快照，沿用设备 token 鉴权。"""
    try:
        path, metadata = resolve_attachment(get_device_agent_turn(session, turn_id), attachment_id)
    except (DeviceAgentError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "附件不存在") from exc
    return FileResponse(path, filename=metadata["filename"], media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store"})
