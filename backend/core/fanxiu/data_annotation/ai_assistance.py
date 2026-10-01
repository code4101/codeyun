"""Ownership-based, cross-process gate for engineering -> Codex incident handoff.

All Fanxiu repair launchers use this boundary. Ownership changes and launch
checks share a lock, so a queued request cannot launch after AI takeover.
Dispatch completion means only that Codex exited, never that the game healed.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from filelock import FileLock

from backend.core.codex import CodexDispatch, CodexEscalationRequest, escalate_to_codex
from backend.core.codex.escalation import inspect_codex_dispatch
from .state import read_data_annotation_json, write_data_annotation_json


def _settings_path(path: Path | None) -> Path:
    # Lazy import avoids cycles from low-level mail and scene modules.
    from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_settings_path
    return path or fanxiu_kernel_scheduler_settings_path()


@lru_cache(maxsize=32)
def _lock(path: str) -> FileLock:
    return FileLock(path, timeout=10)


def assistance_control_lock(settings_path: Path | None = None) -> FileLock:
    """Serialize ownership changes and incident dispatch on one host."""
    path = _settings_path(settings_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return _lock(str(path.with_suffix('.assistance.lock')))


def assistance_enabled(settings: dict) -> bool:
    """Use existing engineering ownership; no second permission flag."""
    return bool(settings.get('job_group_enabled') and settings.get('behavior_tree_enabled', True))


def read_ai_assistance_status(*, scheduler_settings_path: Path | None = None) -> dict:
    """Read persisted handoff diagnostics without launching AI or touching game."""
    path = _settings_path(scheduler_settings_path)
    return read_data_annotation_json(path.with_name('ai_assistance.json'), {}) or {}


def refresh_ai_assistance_status(*, scheduler_settings_path: Path | None = None) -> dict:
    """Explicitly refresh dispatch ownership; never launch or declare healing.

    Use after reconnecting the desktop or inspecting a repair. Failed reads
    preserve the owner, expose the transport cause, and do not start cooldown.
    """
    path = _settings_path(scheduler_settings_path)
    with assistance_control_lock(path):
        state = read_ai_assistance_status(scheduler_settings_path=path)
        previous = state.get('dispatch') or {}
        if not previous.get('dispatch_id'):
            return state
        state_path = path.with_name('ai_assistance.json')
        try:
            status = inspect_codex_dispatch(previous['dispatch_id'])
        except Exception as exc:
            state.update(agent_status='unknown',
                         agent_error=f'{type(exc).__name__}: {exc}',
                         ownership_check_status='failed', ownership_checked_at=time.time())
            write_data_annotation_json(state_path, state)
            raise
        state.update(agent_status=status.status, agent_error=status.error,
                     codex_url=status.codex_url,
                     ownership_check_status='success', ownership_checked_at=time.time())
        write_data_annotation_json(state_path, state)
        return state


def request_ai_assistance(
    request: CodexEscalationRequest, *, scheduler_settings_path: Path | None = None,
) -> CodexDispatch | None:
    """Launch at most one repair agent; return None when disabled or suppressed.

    Different error sources share a single live-agent gate. The same incident
    title has a 30-minute cooldown even after exit/failure, preventing token
    loops. Failed/finished dispatches remain visible and are never marked as
    resolved. No mode change or game operation occurs here.
    """
    from .kernel_scheduler_control import read_scheduler_settings

    path = _settings_path(scheduler_settings_path)
    with assistance_control_lock(path):
        if not path.is_file():
            return None  # No configured owner is not an engineering start.
        settings = read_scheduler_settings(scheduler_settings_path=path)
        if not assistance_enabled(settings):
            return None
        state_path = path.with_name('ai_assistance.json')
        state = read_ai_assistance_status(scheduler_settings_path=path)
        previous = state.get('dispatch') or {}
        if previous.get('dispatch_id'):
            # An unreadable dispatch is not proof its owner exited: fail closed.
            state = refresh_ai_assistance_status(scheduler_settings_path=path)
            if state.get('agent_status') in {'starting', 'running'}:
                return None
        now = time.time()
        key = hashlib.sha256(request.title.encode('utf-8')).hexdigest()
        recent = {k: v for k, v in (state.get('recent_requests') or {}).items()
                  if now - float(v) < 1800}
        # Alternating error sources cannot bypass the same-incident cooldown.
        if state.get('incident_key') and now - float(state.get('requested_at') or 0) < 1800:
            recent[state['incident_key']] = state['requested_at']
        if key in recent or (state.get('status') == 'dispatch_failed'
                             and now - float(state.get('requested_at') or 0) < 1800):
            return None
        recent[key] = now
        state = dict(incident_key=key, title=request.title, requested_at=now,
                     status='dispatching', resolved=False, recent_requests=recent)
        write_data_annotation_json(state_path, state)
        try:
            dispatch = escalate_to_codex(replace(request, constraints=request.constraints + (
                '本任务由工程异常触发。使用调度层公共 API 核对运行权；若另一 AI 已接管，'
                '不得抢占或再派生维修 Agent，应记录冲突并退出。',
                '以实际业务验收为完成依据，Codex 进程结束或输出分析报告不代表故障解决。',
                '凡修故障接管后进入现场调试研发模式：优先保留原失败现场，读取第一现场日志、'
                '真实画面和 Runtime，在现场定位、修复并验证，不要为方便整单重跑先退回 #34。'
                '若现场已回到 #34 等稳定锚点，先从正式业务入口重新触发原问题点；'
                '无法复现时记录证据，不能凭代码推测宣布修复成功。',
                '修复后由本维修 AI 持有运行权完成真实验收：先验证原失败动作，再从正式 Job '
                '入口整单复跑，检查实际业务结果、资源变化和 next_time；再次重入确认幂等、'
                '无重复领取或消耗。已有真实完成凭证应被正确识别，受限项应形成合法终态。'
                '禁止将工程恢复后的下一轮运行用作尚未完成的验收。只有上述验收通过且 Cell '
                '已结束，才允许交还工程调度并完成 Goal。',
            )))
        except Exception as exc:
            state.update(status='dispatch_failed', error=f'{type(exc).__name__}: {exc}')
            write_data_annotation_json(state_path, state)
            raise
        state.update(status='dispatched', dispatch=dispatch.model_dump())
        write_data_annotation_json(state_path, state)
        return dispatch


def report_failed_job(*, task: dict, result: dict, entry_id: str,
                      scheduler_settings_path: Path | None = None) -> dict:
    """Escalate a terminal engineering error, without interpreting game success.

    Domain recovery runs first (e.g. bounded login recovery). This fallback
    covers errors without a dedicated scene/ranking hook. An active repair
    launched by those hooks is deduplicated at the same boundary.
    """
    if result.get('status') != 'error':
        return result
    task_id = str(task.get('id') or '')
    terminal = {key: result.get(key) for key in (
        'status', 'phase', 'error_type', 'error', 'message', 'current_scene', 'evidence_frame_path',
    ) if result.get(key) is not None}
    job = {key: task.get(key) for key in (
        'id', 'task_type', 'label', 'attempt_id', 'next_time', 'last_result',
    ) if task.get(key) is not None}
    request = CodexEscalationRequest(
        title=f"凡修工程作业失败：{task_id}",
        problem=str(result.get('error') or result.get('message') or '正式作业异常结束'),
        objective='修复该作业的真实故障，正式整单复验，并恢复工程自主运行。',
        evidence=(f'Job={task_id}; entry_id={entry_id}',
                  f'终态={terminal!r}', f'原任务={job!r}',
                  '完整第一现场通过 list_scheduler_incidents 与作业日志公共 API 读取。'),
        recovery_instructions=(
            '先核对工程仍持有运行权；已有 AI 接管时退出，不争抢。'
            '通过 take_ai_control 获取运行权，修复后从正式 Job 入口提交完整新 attempt。'
            '真实验收通过后 resume_engineering_control() 交还工程。'),
        completion_criteria=('原故障已修复且正式作业达到合法业务终态',
                             'next_time 正确写回，工程调度恢复'),
    )
    try:
        dispatch = request_ai_assistance(request, scheduler_settings_path=scheduler_settings_path)
        return {**result, 'ai_assistance_dispatch': dispatch.model_dump()} if dispatch else result
    except Exception as exc:
        return {**result, 'ai_assistance_error': f'{type(exc).__name__}: {exc}'}
