from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from backend.core.fanxiu.data_annotation.state import (
    read_data_annotation_json,
    write_data_annotation_json,
)
from backend.core.settings import get_settings


GAME_STATE_INSPECTION_INTERVAL_SECONDS = 60.0
GAME_STATE_INSPECTION_DESCRIPTION = (
    "工程 Scheduler 空闲时通过进程外只读 Runtime 巡检检查游戏状态；命中后只提前 "
    "目标作业触发时间，不与正式作业并行"
)

# 强约束：游戏状态巡检与所有 GUI/视觉/网络技术栈完全正交。这里只允许
# 读取只读动态插桩 Runtime/游戏内存事实；禁止抓包、截图、OCR、
# frame/scene/shape、GUI 状态以及任何兜底。需要视觉识别或操作时必须进入
# 行为树，而不是巡检。
GAME_STATE_INSPECTION_ALLOWED_SOURCES = frozenset({"runtime"})

GameStateProbeReader = Callable[[], dict[str, Any]]
GameStateProbeRecovery = Callable[[], dict[str, Any]]
GameStateDueSink = Callable[[str, datetime], Any]


@dataclass(frozen=True)
class GameStateProbe:
    id: str
    label: str
    source: str
    read: GameStateProbeReader
    recover: GameStateProbeRecovery | None = None

    def __post_init__(self) -> None:
        source = _validate_game_state_probe_source(self.source)
        object.__setattr__(self, "source", source)


_probe_lock = threading.RLock()
_probes: dict[str, GameStateProbe] = {}
_builtin_probes_registered = False
_recovery_lock = threading.RLock()
_recovery_states: dict[str, dict[str, Any]] = {}
_RECOVERY_RUNWAY_SECONDS = 120.0
_RECOVERY_INITIAL_RETRY_SECONDS = 60.0
_RECOVERY_MAX_RETRY_SECONDS = 600.0


def _validate_game_state_probe_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized not in GAME_STATE_INSPECTION_ALLOWED_SOURCES:
        raise ValueError(
            "游戏状态巡检只允许只读动态插桩 Runtime；"
            "禁止抓包、视觉、截图、OCR、场景识别和 GUI 兜底"
        )
    return normalized


def game_state_inspection_state_path() -> Path:
    return _inspection_runtime_dir() / "game_state_inspection.json"


def _inspection_runtime_dir() -> Path:
    path = get_settings().data_dir / "fanxiu" / "data-annotation" / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scheduler_job_group_enabled() -> bool:
    # Product boundary: automatic game-state inspection belongs to Engineering
    # mode.  AI mode pauses the loop so control remains with AI/user.  This
    # lifecycle rule is separate from the probe implementation: probes still
    # read the game-native Runtime and never submit or occupy a Kernel Cell.
    payload = read_data_annotation_json(
        _inspection_runtime_dir() / "scheduler_settings.json",
        {},
    )
    return bool(
        payload.get("job_group_enabled", True)
        if isinstance(payload, dict)
        else True
    )


def _inspection_recovery_allowed() -> tuple[bool, str]:
    from backend.core.fanxiu.data_annotation.behavior_tree_control import (
        read_scheduler_tasks,
    )
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import (
        fanxiu_kernel_manager_status,
    )

    if not _scheduler_job_group_enabled():
        return False, "AI 模式已暂停"
    kernel = fanxiu_kernel_manager_status()
    if not bool(kernel.get("alive")):
        return False, "Kernel 未存活"
    if str(kernel.get("execution_state") or "") != "idle":
        return False, "Kernel 正忙"
    cutoff = datetime.now() + timedelta(seconds=_RECOVERY_RUNWAY_SECONDS)
    for task in read_scheduler_tasks():
        next_time = str(task.get("next_time") or "").strip()
        if not next_time:
            continue
        try:
            due_at = datetime.strptime(next_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if due_at <= cutoff:
            return False, f"两分钟内有到期作业：{task.get('label') or task.get('id')}"
    return True, ""


def _probe_recovery_status(probe_id: str) -> dict[str, Any]:
    with _recovery_lock:
        state = dict(_recovery_states.get(probe_id) or {})
        thread = state.pop("thread", None)
        if thread is not None and thread.is_alive():
            state["status"] = "recovering"
        return {
            "supported": True,
            "status": str(state.get("status") or "healthy"),
            "attempt_count": int(state.get("attempt_count") or 0),
            "failure_count": int(state.get("failure_count") or 0),
            "last_started_at": state.get("last_started_at"),
            "last_finished_at": state.get("last_finished_at"),
            "last_error": str(state.get("last_error") or ""),
            "retry_at": state.get("retry_at"),
            "deferred_reason": str(state.get("deferred_reason") or ""),
        }


def _run_probe_recovery(probe: GameStateProbe) -> None:
    started = time.time()
    try:
        result = probe.recover() if probe.recover is not None else {}
        ok = bool(isinstance(result, dict) and result.get("ok"))
        error = "" if ok else str(
            result.get("reason") if isinstance(result, dict) else ""
        ) or "动态插桩恢复未返回成功状态"
    except Exception as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"
    with _recovery_lock:
        state = _recovery_states.setdefault(probe.id, {})
        failure_count = 0 if ok else int(state.get("failure_count") or 0) + 1
        retry_seconds = min(
            _RECOVERY_MAX_RETRY_SECONDS,
            _RECOVERY_INITIAL_RETRY_SECONDS * (2 ** max(0, failure_count - 1)),
        )
        state.update(
            {
                "status": "recovered" if ok else "error",
                "failure_count": failure_count,
                "last_finished_at": _time_text(datetime.fromtimestamp(time.time())),
                "last_error": error,
                "retry_monotonic": 0.0 if ok else time.monotonic() + retry_seconds,
                "retry_at": (
                    None
                    if ok
                    else _time_text(datetime.fromtimestamp(time.time() + retry_seconds))
                ),
                "deferred_reason": "",
                "recovery_duration_ms": int(round((time.time() - started) * 1000)),
            }
        )


def _schedule_probe_recovery(
    probe: GameStateProbe,
    *,
    asynchronous: bool = True,
) -> dict[str, Any]:
    if probe.recover is None:
        return {"supported": False, "status": "unsupported"}
    with _recovery_lock:
        state = _recovery_states.setdefault(probe.id, {})
        thread = state.get("thread")
        if thread is not None and thread.is_alive():
            return _probe_recovery_status(probe.id)
        if time.monotonic() < float(state.get("retry_monotonic") or 0):
            state["status"] = "backoff"
            return _probe_recovery_status(probe.id)
        # Both sync and async recovery are process-external now. They share the
        # same production runway: engineering mode, idle Kernel, and no Job due
        # within two minutes.
        allowed, reason = _inspection_recovery_allowed()
        if not allowed:
            state.update({"status": "deferred", "deferred_reason": reason})
            return _probe_recovery_status(probe.id)
        if not asynchronous:
            state.update(
                {
                    "status": "recovering",
                    "attempt_count": int(state.get("attempt_count") or 0) + 1,
                    "last_started_at": _time_text(datetime.now()),
                    "deferred_reason": "",
                }
            )
            _run_probe_recovery(probe)
            return _probe_recovery_status(probe.id)
        thread = threading.Thread(
            target=_run_probe_recovery,
            args=(probe,),
            name=f"fanxiu-runtime-recovery-{probe.id}",
            daemon=True,
        )
        state.update(
            {
                "thread": thread,
                "status": "recovering",
                "attempt_count": int(state.get("attempt_count") or 0) + 1,
                "last_started_at": _time_text(datetime.now()),
                "deferred_reason": "",
            }
        )
        thread.start()
        return _probe_recovery_status(probe.id)


def _mark_probe_recovery_healthy(probe: GameStateProbe) -> dict[str, Any]:
    if probe.recover is None:
        return {"supported": False, "status": "unsupported"}
    with _recovery_lock:
        state = _recovery_states.setdefault(probe.id, {})
        thread = state.get("thread")
        if thread is None or not thread.is_alive():
            state.update(
                {
                    "status": "healthy",
                    "failure_count": 0,
                    "retry_monotonic": 0.0,
                    "retry_at": None,
                    "last_error": "",
                    "deferred_reason": "",
                }
            )
    return _probe_recovery_status(probe.id)


def register_game_state_probe(probe: GameStateProbe) -> None:
    probe_id = str(probe.id or "").strip()
    if not probe_id:
        raise ValueError("游戏状态巡检项 id 不能为空")
    _validate_game_state_probe_source(probe.source)
    with _probe_lock:
        _probes[probe_id] = probe


def _ensure_builtin_game_state_probes_registered() -> None:
    global _builtin_probes_registered
    with _probe_lock:
        if _builtin_probes_registered:
            return
        from backend.core.fanxiu.data_annotation.redpacket_state import (
            REDPACKET_STATE_PROBE_ID,
            inspect_redpacket_game_state,
            recover_redpacket_runtime_snapshot,
        )
        from backend.core.fanxiu.data_annotation.seat_mail_state import (
            SEAT_MAIL_PROBE_ID,
            inspect_seat_displacement_mail_state,
        )

        register_game_state_probe(
            GameStateProbe(
                id=REDPACKET_STATE_PROBE_ID,
                label="红包",
                source="runtime",
                read=inspect_redpacket_game_state,
                # The one-minute read path only consumes prewarmed addresses.
                # Cache misses use the shared recovery gate, so discovery runs
                # only while Engineering owns an idle Kernel with no imminent
                # Job, then the same patrol re-reads the fresh Runtime fact.
                recover=recover_redpacket_runtime_snapshot,
            )
        )
        register_game_state_probe(
            GameStateProbe(
                id=SEAT_MAIL_PROBE_ID,
                label="座位被踢邮件",
                source="runtime",
                read=lambda: inspect_seat_displacement_mail_state(
                    defer_cursor_commit=True
                ),
            )
        )
        _builtin_probes_registered = True


def registered_game_state_probes() -> tuple[GameStateProbe, ...]:
    _ensure_builtin_game_state_probes_registered()
    with _probe_lock:
        return tuple(_probes.values())


def _time_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _base_snapshot(*, enabled: bool, interval_seconds: float) -> dict[str, Any]:
    probes = registered_game_state_probes()
    return {
        "ok": True,
        "name": "游戏状态巡检",
        "description": GAME_STATE_INSPECTION_DESCRIPTION,
        "enabled": bool(enabled),
        "status": "running" if enabled else "paused",
        "interval_seconds": max(1.0, float(interval_seconds or GAME_STATE_INSPECTION_INTERVAL_SECONDS)),
        "probe_count": len(probes),
        "probes": [
            {"id": probe.id, "label": probe.label, "source": probe.source}
            for probe in probes
        ],
        "sources": sorted({probe.source for probe in probes if probe.source}),
        "service_pid": os.getpid(),
    }


def inspect_game_state_once(
    *,
    probes: Iterable[GameStateProbe] | None = None,
    due_sink: GameStateDueSink | None = None,
    interval_seconds: float = GAME_STATE_INSPECTION_INTERVAL_SECONDS,
    now: datetime | None = None,
    state_path: Path | None = None,
    asynchronous_recovery: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    checked_at = now or datetime.now()
    selected = tuple(probes) if probes is not None else registered_game_state_probes()
    if due_sink is None:
        from backend.core.fanxiu.data_annotation.behavior_tree_control import (
            set_scheduler_task_next_time,
        )

        due_sink = lambda task_id, due_at: set_scheduler_task_next_time(task_id, due_at)
    for probe in selected:
        _validate_game_state_probe_source(probe.source)
    facts: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    due_task_ids: list[str] = []
    recoveries: dict[str, dict[str, Any]] = {}

    for probe in selected:
        try:
            result = probe.read()
        except Exception as exc:
            errors.append({"probe_id": probe.id, "message": str(exc)})
            continue
        if not isinstance(result, dict):
            errors.append({"probe_id": probe.id, "message": "巡检结果必须是对象"})
            continue
        recovery_required = bool(result.get("recovery_required"))
        recovery = (
            _schedule_probe_recovery(
                probe,
                asynchronous=asynchronous_recovery,
            )
            if recovery_required
            else _mark_probe_recovery_healthy(probe)
        )
        recoveries[probe.id] = recovery
        if (
            recovery_required
            and not asynchronous_recovery
            and str(recovery.get("status") or "") == "recovered"
        ):
            try:
                refreshed = probe.read()
            except Exception as exc:
                errors.append({"probe_id": probe.id, "message": str(exc)})
                continue
            if not isinstance(refreshed, dict):
                errors.append({"probe_id": probe.id, "message": "恢复后巡检结果必须是对象"})
                continue
            result = refreshed
        if result.get("ok") is False:
            errors.append({
                "probe_id": probe.id,
                "message": str(result.get("message") or "Runtime 状态不可用"),
            })
        probe_facts = result.get("facts")
        if isinstance(probe_facts, dict):
            facts[probe.id] = probe_facts
        due_failed = False
        for value in result.get("due_task_ids") or []:
            task_id = str(value or "").strip()
            if not task_id or task_id in due_task_ids:
                continue
            due_task_ids.append(task_id)
            if due_sink is not None:
                try:
                    due_sink(task_id, checked_at)
                except Exception as exc:
                    due_failed = True
                    errors.append({
                        "probe_id": probe.id,
                        "message": f"提前作业 {task_id} 失败：{exc}",
                    })
        commit_after_due = result.get("_commit_after_due")
        if callable(commit_after_due) and not due_failed:
            try:
                commit_after_due()
            except Exception as exc:
                errors.append({
                    "probe_id": probe.id,
                    "message": f"提交巡检游标失败：{exc}",
                })

    duration_ms = int(round((time.monotonic() - started) * 1000))
    status = "error" if errors else "running"
    snapshot = {
        **_base_snapshot(enabled=True, interval_seconds=interval_seconds),
        "status": status,
        "probe_count": len(selected),
        "probes": [
            {"id": probe.id, "label": probe.label, "source": probe.source}
            for probe in selected
        ],
        "sources": sorted({probe.source for probe in selected if probe.source}),
        "last_checked_at": _time_text(checked_at),
        "next_check_at": _time_text(
            checked_at + timedelta(seconds=max(1.0, float(interval_seconds or GAME_STATE_INSPECTION_INTERVAL_SECONDS)))
        ),
        "last_result": "empty" if not selected else ("error" if errors else "success"),
        "last_message": (
            "暂无巡检项"
            if not selected
            else (
                "巡检异常：" + "；".join(
                    f"{item['probe_id']}：{item['message']}" for item in errors[:2]
                )
                if errors
                else (
                    f"已检查 {len(selected)} 项，提前 {len(due_task_ids)} 个作业"
                    if due_task_ids
                    else f"已检查 {len(selected)} 项"
                )
            )
        ),
        "last_duration_ms": duration_ms,
        "facts": facts,
        "errors": errors,
        "recoveries": recoveries,
        "due_task_ids": due_task_ids,
        "updated_at": time.time(),
    }
    write_data_annotation_json(state_path or game_state_inspection_state_path(), snapshot)
    return snapshot


def read_game_state_inspection_status(
    *,
    interval_seconds: float = GAME_STATE_INSPECTION_INTERVAL_SECONDS,
    state_path: Path | None = None,
) -> dict[str, Any]:
    enabled = _scheduler_job_group_enabled()
    payload = read_data_annotation_json(state_path or game_state_inspection_state_path(), {})
    snapshot = dict(payload) if isinstance(payload, dict) else {}
    result = {**_base_snapshot(enabled=enabled, interval_seconds=interval_seconds), **snapshot}
    result["enabled"] = enabled
    if not enabled:
        result["status"] = "paused"
        result["next_check_at"] = None
    elif not result.get("updated_at") or result.get("status") == "paused":
        result["status"] = "starting"
    elif time.time() - float(result.get("updated_at") or 0) > max(180.0, interval_seconds * 3):
        result["status"] = "unavailable"
    return result
