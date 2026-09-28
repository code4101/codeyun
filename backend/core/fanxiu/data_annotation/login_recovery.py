"""Login progress and persistent app-recovery budget; no game I/O here.

Only a positively identified login/loading page can request recovery. The
Scheduler consumes that request after the old attempt ends, under its dispatch
lease. Two app restarts are allowed until a real login succeeds; a new Cell,
Kernel, process or invocation does not replenish the budget.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock


class FanxiuLoginStalled(RuntimeError):
    """Known login page made no progress; finish this Cell before recovery."""


@dataclass
class LoginProgress:
    timeout_seconds: float = 300.0
    phase: str = ""
    high_water: float | None = None
    changed_at: float | None = None

    def observe(self, phase: str, now: float, progress: float | None = None) -> float:
        """Return seconds without progress. OCR losses/regressions do not reset it."""
        if phase != self.phase or self.changed_at is None:
            self.phase, self.high_water, self.changed_at = phase, progress, now
        elif progress is not None and (self.high_water is None or progress > self.high_water):
            self.high_water, self.changed_at = progress, now
        return max(0.0, now - self.changed_at)


def loading_progress(text: str) -> float | None:
    """Read the percentage only after the caller proved a resource-loading page."""
    matches = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[%％]", text)
    values = [float(value) for value in matches if 0 <= float(value) <= 100]
    return values[0] if len(values) == 1 else None


def reserve_login_recovery(path: Path, *, now: float) -> dict:
    """Atomically reserve before side effects; failed launches consume a slot too.

    State lives beside Scheduler state, never in a Kernel namespace. A cooldown
    denial also ends the automatic recovery loop, so callers cannot busy-wait.
    Invalid state fails closed instead of silently granting fresh restarts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=5):
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(state, dict):
            raise ValueError("登录恢复次数记录无效，需诊断后恢复")
        count = int(state.get("attempts", 0))
        last = float(state.get("last_restart_at", 0))
        if count < 0 or not math.isfinite(last) or last < 0:
            raise ValueError("登录恢复次数记录无效，需诊断后恢复")
        if count >= 2:
            raise RuntimeError("登录恢复已连续重启游戏 2 次仍未成功，停止自动重启并升级诊断")
        if count and now - last < 300:
            raise RuntimeError("登录恢复处于 300 秒冷却期，停止本轮自动重启")
        state.update(attempts=count + 1, last_restart_at=now)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
        return state


def clear_login_recovery(path: Path) -> None:
    """Replenish only after the formal login task has reached its real terminal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=5):
        path.unlink(missing_ok=True)


def request_login_recovery_diagnosis(path: Path, *, detail: str, entry_id: str, task_id: str,
                                    scheduler_settings_path: Path | None = None) -> dict:
    """Request diagnosis through the shared ownership/deduplication gate.

    Persist the receipt, but do not permanently latch a finished/crashed Agent:
    the common gate owns process liveness and bounded retry after cooldown.
    """
    from backend.core.codex import CodexEscalationRequest
    from .ai_assistance import request_ai_assistance

    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=5):
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        dispatch = request_ai_assistance(CodexEscalationRequest(
            title="凡修登录卡滞：有界游戏恢复未完成",
            problem=detail,
            objective="定位并修复登录卡滞根因，通过正式登录作业验收并恢复工程运行。",
            evidence=(f"恢复次数记录：{path}", f"Scheduler 作业：{task_id}", detail),
            attempted_actions=("已完成的游戏重启次数及冷却时间以恢复次数记录为准",),
            recovery_instructions=(
                f"按凡修技能先用 take_ai_control({entry_id!r}) 取得运行权。"
                "确认旧 Cell 已结束，再诊断游戏进程、加载与模拟器状态；"
                "只在模拟器本体异常时重启模拟器，不得清零次数后盲目重复游戏重启。"
                f"修复后从正式 Scheduler 入口运行 {task_id} 新 attempt，成功后交还工程调度。"
            ),
            completion_criteria=("正式登录作业达到真实终态", "恢复次数已由登录成功清零", "工程运行已恢复"),
        ), scheduler_settings_path=scheduler_settings_path)
        if dispatch is None:
            return {}
        state["diagnosis_dispatch"] = dispatch.model_dump()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(path)
        return state["diagnosis_dispatch"]
