"""Versioned thin-client API. Account grants expose recognition, not host control.

Register/login through /api/auth, then have an administrator allow fanxiu.remote
in the existing feature-access UI. Every request rechecks account and permission;
neither a local-device token nor possession of another user's session grants access.
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from sqlmodel import Session

from backend.core.access.auth import get_current_active_user
from backend.core.access.feature_access_guard import ensure_feature_access
from backend.core.fanxiu.remote.sessions import (
    ACTION_TTL_SECONDS, MAX_STEPS, SESSION_IDLE_SECONDS, RemoteError, remote_sessions,
)
from backend.core.fanxiu.remote.observation import MAX_IMAGE_BYTES
from backend.core.fanxiu.remote.runtime_observation import RuntimeSnapshot
from backend.db import get_session
from backend.models import User

MAX_BODY_BYTES = 9 * 1024 * 1024
logger = logging.getLogger(__name__)


class BoundedBodyRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                limit = 45 * 1024 * 1024 if request.url.path.endswith("/results") else MAX_BODY_BYTES
                if size > limit:
                    raise HTTPException(413, "截图请求过大")
                chunks.append(chunk)
            body = b"".join(chunks)

            async def receive():
                return {"type": "http.request", "body": body, "more_body": False}

            return await original(Request(request.scope, receive))

        return bounded


def remote_user(current_user: User = Depends(get_current_active_user),
                session: Session = Depends(get_session)) -> User:
    ensure_feature_access(session, feature_key="fanxiu.remote", current_user=current_user)
    return current_user


router = APIRouter(tags=["fanxiu-remote"], route_class=BoundedBodyRoute)


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSession(StrictRequest):
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:@-]+$")
    job_id: Literal["observe", "navigate", "runtime_probe"]
    target_scene_id: int | None = Field(default=None, gt=0)


class StepRequest(StrictRequest):
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    frame_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    # frame_id correlates this observation, including a Runtime-only observation.
    image_base64: str | None = Field(default=None, min_length=4, max_length=8 * 1024 * 1024)
    runtime_snapshot: RuntimeSnapshot | None = None
    previous_action_id: str | None = Field(default=None, max_length=64)
    previous_action_status: Literal["executed", "failed", "uncertain"] | None = None

    @model_validator(mode="after")
    def receipt_pair(self):
        if bool(self.previous_action_id) != bool(self.previous_action_status):
            raise ValueError("动作编号和执行结果必须同时提供")
        return self


class RemoteAction(BaseModel):
    action_id: str
    frame_id: str
    kind: Literal["tap", "swipe", "wait", "collect_runtime"]
    query: Literal["process"] | None = None
    expires_at: float
    x: StrictInt | None = None
    y: StrictInt | None = None
    start_x: StrictInt | None = None
    start_y: StrictInt | None = None
    end_x: StrictInt | None = None
    end_y: StrictInt | None = None
    duration_ms: int | None = None
    wait_ms: int | None = None


class StepResponse(BaseModel):
    session_id: str
    request_id: str
    frame_id: str
    status: Literal["running", "completed", "blocked"]
    observation: dict = Field(default_factory=dict)
    action: RemoteAction | None = None
    reason: str | None = None
    expires_at: float
    next_run_at: float | None = None


class SessionResponse(BaseModel):
    session_id: str
    expires_at: float
    job_id: Literal["observe", "navigate", "runtime_probe"]
    device_id: str
    status: Literal["running"]


def _call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except RemoteError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get("/capabilities")
def capabilities(user: User = Depends(remote_user)):
    from backend.core.fanxiu.client.mumu_control import DEFAULT_FIXED_WIDTH, DEFAULT_FIXED_HEIGHT
    return {"protocol_version": 1, "feature_key": "fanxiu.remote",
            "jobs": [{"id": "observe", "title": "识别当前画面"},
                     {"id": "navigate", "title": "前往已标注场景", "requires": ["target_scene_id"]},
                     {"id": "runtime_probe", "title": "按需验证本机 Runtime 内存读取", "requires": ["runtime.process"]}],
            "observation_channels": ["screenshot", "runtime"],
            "screenshot_geometry": {"reference_width": DEFAULT_FIXED_WIDTH,
                                    "reference_height": DEFAULT_FIXED_HEIGHT,
                                    "aspect_ratio": "9:16", "minimum_width": 540,
                                    "action_coordinates": "source_pixels",
                                    "requires_scene_match": True},
            "runtime_queries": ["process"],
            "limits": {"max_image_bytes": MAX_IMAGE_BYTES, "max_steps": MAX_STEPS,
                       "action_ttl_seconds": ACTION_TTL_SECONDS, "session_idle_seconds": SESSION_IDLE_SECONDS},
            "user": {"id": user.id, "username": user.username}}


@router.post("/sessions", status_code=201, response_model=SessionResponse)
def create_session(payload: CreateSession, user: User = Depends(remote_user)):
    return _call(remote_sessions.create, user.id, **payload.model_dump())


@router.post("/sessions/{session_id}/step", response_model=StepResponse, response_model_exclude_none=True)
def step_session(session_id: str, payload: StepRequest, user: User = Depends(remote_user)):
    # Authenticate and check ownership before expensive decoding/recognition.
    attempt = _call(remote_sessions.get, user.id, session_id)
    if not payload.image_base64 and attempt.job_id != "runtime_probe":
        raise HTTPException(422, "此作业需要截图观测")
    try:
        image = base64.b64decode(payload.image_base64, validate=True) if payload.image_base64 else b""
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(422, "截图必须是有效的纯 Base64 编码") from exc
    if len(image) > MAX_IMAGE_BYTES or (payload.image_base64 and not image):
        raise HTTPException(413, "截图大小超出限制")
    from backend.core.fanxiu.remote.observation import plan_remote_observation

    try:
        return _call(remote_sessions.step, user.id, session_id, payload.model_dump(),
                     image, plan_remote_observation)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, "截图格式或尺寸无效") from exc
    except Exception as exc:
        logger.exception("Remote observation failed for session %s", session_id)
        raise HTTPException(503, "远程识别暂时不可用，请暂停后重新尝试") from exc


@router.delete("/sessions/{session_id}")
def close_session(session_id: str, user: User = Depends(remote_user)):
    return _call(remote_sessions.close, user.id, session_id)


def bridge_user(user: User = Depends(remote_user)) -> User:
    if not user.is_superuser:
        raise HTTPException(403, "设备执行桥目前仅供超级管理员本人测试")
    return user


class ConnectDevice(StrictRequest):
    device_id: str = Field(min_length=1, max_length=128)
    client_instance_id: str = Field(min_length=1, max_length=128)
    metadata: dict = Field(default_factory=dict)


class PollDevice(StrictRequest):
    wait_seconds: float = Field(default=20, ge=0, le=25)


class DeviceResult(StrictRequest):
    request_id: str = Field(min_length=1, max_length=64)
    status: Literal["ok", "error", "uncertain"]
    result: dict = Field(default_factory=dict)
    error: str = Field(default="", max_length=2000)


class DeviceRpc(StrictRequest):
    worker_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=1, max_length=64)
    operation: str = Field(max_length=32)
    args: dict = Field(default_factory=dict)
    timeout_s: float = Field(default=30, ge=1, le=120)


@router.post("/devices/connect")
def connect_device(payload: ConnectDevice, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    return _call(device_bridge.connect, user.id, **payload.model_dump())


@router.post("/devices/{worker_id}/poll")
def poll_device(worker_id: str, payload: PollDevice, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    return _call(device_bridge.poll, user.id, worker_id, payload.wait_seconds)


@router.post("/devices/{worker_id}/results")
def device_result(worker_id: str, payload: DeviceResult, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    return _call(device_bridge.result, user.id, worker_id, **payload.model_dump())


@router.delete("/devices/{worker_id}")
def disconnect_device(worker_id: str, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    return _call(device_bridge.close, user.id, worker_id)


@router.post("/device-rpc")
def device_rpc(payload: DeviceRpc, request: Request):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    # This is server-internal RPC, not a substitute for worker user login.
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "设备 RPC 仅限服务端本机调用")
    token = request.headers.get("X-Fanxiu-Bridge", "")
    return _call(device_bridge.signed_rpc, token, **payload.model_dump())


class RunNextDevice(StrictRequest):
    stop_at: str = Field(min_length=10, max_length=64)


@router.post("/devices/{worker_id}/run-next")
def run_next_device(worker_id: str, payload: RunNextDevice, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    from backend.core.fanxiu.remote.job_worker import job_worker
    _call(device_bridge.describe, user.id, worker_id)
    return _call(job_worker.next, worker_id, payload.stop_at)


@router.get("/devices/{worker_id}/jobs")
def device_jobs(worker_id: str, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    from backend.core.fanxiu.remote.job_worker import job_worker
    _call(device_bridge.describe, user.id, worker_id)
    return job_worker.status(worker_id)


@router.post("/devices/{worker_id}/resume-jobs")
def resume_device_jobs(worker_id: str, user: User = Depends(bridge_user)):
    from backend.core.fanxiu.remote.device_bridge import device_bridge
    from backend.core.fanxiu.remote.job_worker import job_worker
    _call(device_bridge.describe, user.id, worker_id)
    return _call(job_worker.resume, worker_id)
