"""Bounded, ephemeral remote attempts, independent of the host Kernel/Scheduler.

Clients own scheduling and the physical device lock. This service owns attempt
isolation, frame/action correlation and request deduplication. Restart loses an
attempt deliberately: clients must pause and start from a fresh observation.
An action receipt reports delivery, never proves the game's business outcome.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Any


SESSION_IDLE_SECONDS = 120
SESSION_MAX_SECONDS = 600
ACTION_TTL_SECONDS = 15
MAX_STEPS = 120
MAX_SESSIONS = 64
MAX_USER_SESSIONS = 2


class RemoteError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass
class Attempt:
    user_id: int
    device_id: str
    job_id: str
    target_scene_id: int | None
    created_at: float
    expires_at: float
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lock: Any = field(default_factory=threading.Lock)
    status: str = "running"
    steps: int = 0
    pending_action_id: str | None = None
    pending_runtime_query: str | None = None
    last_request_id: str | None = None
    last_fingerprint: str | None = None
    last_response: dict | None = None
    seen_requests: set[str] = field(default_factory=set)
    seen_frames: set[str] = field(default_factory=set)


class RemoteSessions:
    """Single-process leases. Run this router on CodeYun's single backend worker.

    No screenshots, credentials or game cursors are persisted. Only the most
    recent response may be replayed; older requests are rejected, never re-run.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.attempts: dict[str, Attempt] = {}
        self.recognition_slots = threading.BoundedSemaphore(2)

    def create(self, user_id: int, device_id: str, job_id: str,
               target_scene_id: int | None = None) -> dict:
        if job_id not in {"observe", "navigate", "runtime_probe"}:
            raise RemoteError(422, "不支持的远程作业")
        if job_id == "navigate" and (target_scene_id is None or target_scene_id <= 0):
            raise RemoteError(422, "导航需要目标场景编号")
        now = self.clock()
        with self.lock:
            # Never evict in-flight work; its per-attempt lock protects its lease.
            for key, item in list(self.attempts.items()):
                if item.expires_at <= now and item.lock.acquire(blocking=False):
                    try:
                        del self.attempts[key]
                    finally:
                        item.lock.release()
            owned = [a for a in self.attempts.values() if a.user_id == user_id]
            if any(a.device_id == device_id and a.status == "running" for a in owned):
                raise RemoteError(409, "该设备已有远程作业，请先停止或等待租约到期")
            if len(owned) >= MAX_USER_SESSIONS or len(self.attempts) >= MAX_SESSIONS:
                raise RemoteError(429, "远程会话数量已达上限，请先释放旧会话")
            attempt = Attempt(user_id, device_id, job_id, target_scene_id,
                              now, now + SESSION_IDLE_SECONDS)
            self.attempts[attempt.session_id] = attempt
        return {"session_id": attempt.session_id, "expires_at": attempt.expires_at,
                "job_id": job_id, "device_id": device_id, "status": "running"}

    def get(self, user_id: int, session_id: str) -> Attempt:
        with self.lock:
            attempt = self.attempts.get(session_id)
        if attempt is None or attempt.user_id != user_id or attempt.expires_at <= self.clock():
            raise RemoteError(404, "远程会话不存在或已过期，请从新截图重新开始")
        return attempt

    def close(self, user_id: int, session_id: str) -> dict:
        attempt = self.get(user_id, session_id)
        with attempt.lock:
            attempt.status = "blocked"
            with self.lock:
                self.attempts.pop(session_id, None)
        return {"session_id": session_id, "status": "closed"}

    def step(self, user_id: int, session_id: str, payload: dict,
             image_bytes: bytes, planner: Callable[..., dict]) -> dict:
        attempt = self.get(user_id, session_id)
        if not attempt.lock.acquire(blocking=False):
            raise RemoteError(409, "该会话正在处理另一帧")
        try:
            now = self.clock()
            if attempt.expires_at <= now:
                raise RemoteError(404, "远程会话已过期")
            request_id = payload["request_id"]
            fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            if request_id == attempt.last_request_id:
                if fingerprint != attempt.last_fingerprint:
                    raise RemoteError(409, "同一请求编号不能对应不同内容")
                return copy.deepcopy(attempt.last_response)
            if request_id in attempt.seen_requests:
                raise RemoteError(409, "旧请求已经处理，禁止重放")
            if payload["frame_id"] in attempt.seen_frames:
                raise RemoteError(409, "新请求必须使用新截图编号")
            if attempt.status != "running":
                raise RemoteError(409, "作业已经结束，请创建新会话")
            previous_id = payload.get("previous_action_id")
            previous_status = payload.get("previous_action_status")
            if attempt.pending_action_id:
                if previous_id != attempt.pending_action_id or not previous_status:
                    raise RemoteError(409, "必须上报上一动作结果后才能处理下一帧")
            elif previous_id or previous_status:
                raise RemoteError(409, "当前会话没有待确认动作")
            runtime_snapshot = payload.get("runtime_snapshot")
            if runtime_snapshot is not None:
                if not attempt.pending_runtime_query or runtime_snapshot.get("query") != attempt.pending_runtime_query:
                    raise RemoteError(409, "Runtime 观测必须对应服务端本次请求的命名查询")
            if attempt.pending_runtime_query and previous_status == "executed" and runtime_snapshot is None:
                raise RemoteError(409, "已完成的 Runtime 读取必须提供观测结果")

            if previous_status in {"failed", "uncertain"}:
                result = {"status": "blocked", "reason": "上一动作结果不确定，已暂停；请重新观察后启动新作业", "observation": {}}
            elif attempt.steps >= MAX_STEPS or now - attempt.created_at >= SESSION_MAX_SECONDS:
                result = {"status": "blocked", "reason": "已达到本次作业运行预算", "observation": {}}
            else:
                if not self.recognition_slots.acquire(blocking=False):
                    raise RemoteError(429, "识别服务繁忙，请稍后重试当前请求")
                try:
                    result = planner(image_bytes, job_id=attempt.job_id,
                                     target_scene_id=attempt.target_scene_id,
                                     runtime_snapshot=runtime_snapshot)
                finally:
                    self.recognition_slots.release()

            response = {**result, "session_id": session_id, "request_id": request_id,
                        "frame_id": payload["frame_id"]}
            action = response.get("action")
            if response.get("status") not in {"running", "completed", "blocked"}:
                raise RemoteError(500, "远程规划器返回无效状态")
            if action:
                if response["status"] != "running" or action.get("kind") not in {"tap", "swipe", "wait", "collect_runtime"}:
                    raise RemoteError(500, "远程规划器返回无效动作")
                response["action"] = {**action, "action_id": uuid.uuid4().hex,
                                      "frame_id": payload["frame_id"],
                                      "expires_at": self.clock() + ACTION_TTL_SECONDS}
            elif response["status"] == "running":
                raise RemoteError(500, "运行中的远程规划器必须返回动作或等待")
            attempt.pending_action_id = response.get("action", {}).get("action_id")
            attempt.pending_runtime_query = (response.get("action", {}).get("query")
                                             if response.get("action", {}).get("kind") == "collect_runtime" else None)
            attempt.steps += 1
            attempt.status = response["status"]
            attempt.expires_at = min(self.clock() + SESSION_IDLE_SECONDS,
                                     attempt.created_at + SESSION_MAX_SECONDS + SESSION_IDLE_SECONDS)
            response["expires_at"] = attempt.expires_at
            attempt.last_request_id = request_id
            attempt.last_fingerprint = fingerprint
            attempt.last_response = copy.deepcopy(response)
            attempt.seen_requests.add(request_id)
            attempt.seen_frames.add(payload["frame_id"])
            return response
        finally:
            attempt.lock.release()


remote_sessions = RemoteSessions()
