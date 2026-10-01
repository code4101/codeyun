"""Yield to the account's human operator, then reconnect only for due work.

The other-login notice is an intentional 30-minute handoff, not a broken game
requiring app restarts or an AI incident. Its wall-clock deadline survives Cell,
Kernel and service restarts. Reobserving the same notice never extends it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import math
import time

from filelock import FileLock

from .state import read_data_annotation_json, write_data_annotation_json

EXTERNAL_LOGIN_NOTICE_SCENE_ID = 909
EXTERNAL_LOGIN_WAIT_SECONDS = 30 * 60


class FanxiuExternalLoginWait(RuntimeError):
    """Lawful deferral: preserve the notice and finish the current Cell."""

    def __init__(self, state: dict):
        self.resume_at = float(state["resume_at"])
        self.next_time = datetime.fromtimestamp(self.resume_at).strftime("%Y-%m-%d %H:%M:%S")
        super().__init__(f"账号由代玩操作，最早 {self.next_time}；届时有到期作业才接回登录")


def external_login_handoff_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_state_path
    return fanxiu_kernel_scheduler_state_path().with_name("external_login_handoff.json")


def read_external_login_handoff(*, path: Path | None = None, now: float | None = None) -> dict:
    """Read the wait gate only; expiry never starts or schedules login."""
    target = external_login_handoff_path(path)
    state = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    if not isinstance(state, dict):
        raise RuntimeError("账号交接记录格式错误，保留现场")
    if state:
        if state.get("status") not in {"waiting", "completed"}:
            raise RuntimeError("账号交接状态未知，保留现场")
        start, end = float(state["detected_at"]), float(state["resume_at"])
        if not math.isfinite(start) or not math.isfinite(end) or end < start + EXTERNAL_LOGIN_WAIT_SECONDS:
            raise RuntimeError("账号交接截止时间无效，保留现场")
    current = time.time() if now is None else now
    waiting = state.get("status") == "waiting"
    return {**state, "blocked": bool(waiting and current < float(state["resume_at"])),
            "waiting_for_due_job": bool(waiting and current >= float(state["resume_at"]))}


def observe_external_login_notice(*, evidence: dict | None = None, path: Path | None = None,
                                  now: float | None = None) -> dict:
    """Atomically start one handoff; keep its deadline until verified login."""
    target = external_login_handoff_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    current = time.time() if now is None else now
    with FileLock(str(target.with_suffix(".lock")), timeout=5):
        state = read_external_login_handoff(path=target, now=current)
        if state.get("status") != "waiting":
            state = {"status": "waiting", "detected_at": current,
                     "resume_at": current + EXTERNAL_LOGIN_WAIT_SECONDS,
                     "evidence": dict(evidence or {})}
            write_data_annotation_json(target, state)
    state = read_external_login_handoff(path=target, now=current)
    # The receipt fixes the deadline; ordinary Scheduler timestamps do the
    # waiting. Expiry creates neither a login Job nor a separate timer.
    if path is None:
        from .kernel_scheduler_control import defer_scheduler_tasks_until
        defer_scheduler_tasks_until(datetime.fromtimestamp(float(state["resume_at"])))
    return state


def require_external_login_wait_finished(*, path: Path | None = None, now: float | None = None) -> dict:
    state = read_external_login_handoff(path=path, now=now)
    if state.get("blocked"):
        raise FanxiuExternalLoginWait(state)
    return state


def complete_external_login_handoff(*, path: Path | None = None) -> None:
    """Clear the handoff only after the standard login's verified terminal."""
    target = external_login_handoff_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(target.with_suffix(".lock")), timeout=5):
        state = read_external_login_handoff(path=target)
        if state.get("status") == "waiting":
            if state.get("blocked"):
                raise FanxiuExternalLoginWait(state)
            state.update(status="completed", completed_at=time.time())
            state.pop("blocked", None)
            state.pop("waiting_for_due_job", None)
            write_data_annotation_json(target, state)
