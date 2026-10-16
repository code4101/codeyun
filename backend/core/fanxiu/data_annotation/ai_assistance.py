"""Ownership-based, cross-process gate for Fanxiu incident main-agent handoff.

All Fanxiu repair launchers use this boundary. Ownership changes and launch
checks share a lock, so a queued request cannot launch after AI takeover.
Dispatch completion means only that the main agent's transport exited, never
that the game healed. The public dispatch boundary selects the business main
agent; Claude owns a durable session and independently checked acceptance.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from filelock import FileLock

from backend.core.agents.dispatch import dispatch_agent_task
from backend.core.agents.contracts import AgentDispatch as CodexDispatch, AgentTaskRequest as CodexEscalationRequest
from backend.core.agents.dispatch import inspect_agent_dispatch as inspect_codex_dispatch
from .state import read_data_annotation_json, write_data_annotation_json

# Legacy monkeypatch name: kept as an alias for the public main-agent boundary.
escalate_to_codex = dispatch_agent_task


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


def verify_engineering_control_returned(record: dict) -> tuple[bool, str]:
    """Independent acceptance gate: read ownership and exact formal job evidence.

    Agent receipts must additionally supply ``job_verifications`` for repairs:
    task_id and last_run_at from read_scheduler_tasks, plus next_time. These must
    match current persisted facts and postdate this incident. Domain conclusions
    and idempotence evidence remain required in each criterion's receipt.
    """
    from datetime import datetime
    from .kernel_scheduler_control import read_scheduler_settings, read_scheduler_tasks
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status
    settings = read_scheduler_settings()
    kernel = fanxiu_kernel_manager_status()
    if not settings.get('job_group_enabled') or not settings.get('behavior_tree_enabled', True) or not kernel.get('alive'):
        return False, 'Engineering ownership/live Kernel not confirmed'
    proofs = (record.get('receipt') or {}).get('job_verifications')
    if not isinstance(proofs, list) or not proofs:
        return False, 'Formal job_verifications receipt required'
    expected_task = (record['request'].get('acceptance_context') or {}).get('task_id')
    if expected_task and expected_task not in {p.get('task_id') for p in proofs}:
        return False, 'Receipt does not verify the original failed Job'
    tasks = {str(t['id']): t for t in read_scheduler_tasks()}
    created = datetime.fromisoformat(record['created_at']).timestamp()
    for proof in proofs:
        task = tasks.get(str(proof.get('task_id')))
        if not task or task.get('last_result') != 'success' or task.get('attempt_id'):
            return False, 'Exact formal Job is not a successful terminal attempt'
        if task.get('last_run_at') != proof.get('last_run_at') or task.get('next_time') != proof.get('next_time'):
            return False, 'Formal Job receipt no longer matches persisted scheduling facts'
        try:
            when = datetime.fromisoformat(str(task['last_run_at'])).timestamp()
        except (KeyError, ValueError, TypeError):
            return False, 'Formal Job completion time unavailable'
        if when < created:
            return False, 'Historical success predates this repair'
    return True, 'Engineering ownership returned; new formal successful Jobs and next_time independently matched'


def retire_legacy_assistance(*, expected_dispatch_id: str, reason: str,
                             scheduler_settings_path: Path | None = None,
                             writer_snapshot: dict | None = None) -> dict:
    """Explicit provider migration only; preserve unfinished incident, never mark healed.

    The operator supplies the exact previous ID. A bounded public desktop read
    must show no active writer; a busy Kernel or active attempt refuses migration.
    A caller with a live desktop connector may supply its fresh raw read_thread
    response when the retired provider's stored desktop binding no longer exists.
    This is not called from a scheduler timer or an ordinary status read.
    """
    from backend.core.codex.desktop import call_desktop_tool
    from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status
    from .kernel_scheduler_control import read_scheduler_tasks
    if not reason.strip():
        raise ValueError('Explicit migration reason required')
    path = _settings_path(scheduler_settings_path)
    with assistance_control_lock(path):
        state = read_ai_assistance_status(scheduler_settings_path=path)
        previous = state.get('dispatch') or {}
        if previous.get('dispatch_id') != expected_dispatch_id:
            raise ValueError('Previous dispatch changed; migration refused')
        if previous.get('provider') == 'claude':
            raise ValueError('Only legacy Codex ownership may be retired here')
        kernel = fanxiu_kernel_manager_status()
        if kernel.get('execution_state') == 'busy' or any(t.get('last_result') == 'running' for t in read_scheduler_tasks()):
            raise RuntimeError('Game still has a live execution owner')
        if previous.get('pid'):
            import psutil
            if psutil.pid_exists(int(previous['pid'])):
                raise RuntimeError('Previous CLI owner may still be alive')
        if previous.get('thread_id'):
            evidence = writer_snapshot or call_desktop_tool('read_thread',
                {'threadId': previous['thread_id'], 'turnLimit': 1}, timeout=15)
            if (evidence.get('thread') or {}).get('id') != previous['thread_id']:
                raise ValueError('Writer snapshot belongs to another thread')
            status = (evidence.get('thread', {}).get('status') or {}).get('type')
            turns = evidence.get('turns') or []
            if status not in {'idle', 'notLoaded', 'not_loaded'} or (turns and turns[0].get('status') not in {'completed', 'failed', 'interrupted'}):
                raise RuntimeError('Previous desktop writer is not confirmed inactive')
        retired = {**state, 'retired_at': time.time(), 'retired_reason': reason,
                   'resolution': 'superseded_provider_unresolved',
                   'writer_snapshot': evidence if previous.get('thread_id') else None}
        state = {'status': 'provider_migrated', 'resolved': False, 'retired_incidents':
                 [*(state.get('retired_incidents') or []), retired], 'recent_requests': {}}
        write_data_annotation_json(path.with_name('ai_assistance.json'), state)
        return state


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
                     agent_progress_diagnostics=getattr(status, 'progress_diagnostics', None),
                     codex_url=status.codex_url,
                     agent_provider=getattr(status, 'provider', 'codex'),
                     agent_session_id=getattr(status, 'thread_id', None),
                     agent_acceptance=getattr(status, 'acceptance', None),
                     ownership_check_status='success', ownership_checked_at=time.time())
        write_data_annotation_json(state_path, state)
        return state


def _isolated_world_facts(scheduler_settings_path: Path | None) -> Path | None:
    """World facts beside an explicit (test/isolated) settings file; production uses the default."""
    return Path(scheduler_settings_path).with_name('world_facts.json') if scheduler_settings_path is not None else None


def request_ai_assistance(
    request: CodexEscalationRequest, *, scheduler_settings_path: Path | None = None,
    mode: str = 'repair', incident_hold_key: str | None = None,
) -> CodexDispatch | None:
    """Launch at most one main agent; return None when disabled or suppressed.

    Different error sources share a single live-agent gate. The same incident
    title has a 30-minute cooldown even after exit/failure, preventing token
    loops. Failed/finished dispatches remain visible and are never marked as
    resolved. No mode change or game operation occurs here.
    read_only_research replaces the repair recovery instructions with a strict
    no-Cell/no-takeover boundary, while retaining the same durable launch gate.
    The request is tagged with the Fanxiu business so the public dispatch
    boundary selects the configured main agent and its resources.
    """
    if mode not in {'repair', 'read_only_research', 'maintenance'}:
        raise ValueError('Unknown assistance mode')
    if mode == 'repair' and not request.acceptance_context.get('task_id'):
        from .subtask_execution import current_job_log_context
        task_id = current_job_log_context().get('task_id')
        if task_id:
            request = replace(request, acceptance_context={**request.acceptance_context, 'task_id': task_id})
    from .kernel_scheduler_control import read_scheduler_settings

    path = _settings_path(scheduler_settings_path)
    with assistance_control_lock(path):
        if not path.is_file():
            return None  # No configured owner is not an engineering start.
        settings = read_scheduler_settings(scheduler_settings_path=path)
        if not assistance_enabled(settings):
            # A runtime error closes the gate before any launch. Its own durable
            # hold (engineering origin, launch pending) is the only authority to
            # launch with the gate closed; AI-owned failures never qualify.
            from .kernel_scheduler_control import runtime_error_launch_authorized
            if not (settings.get('behavior_tree_enabled', True) and runtime_error_launch_authorized(
                    incident_hold_key, world_facts_path=_isolated_world_facts(scheduler_settings_path))):
                return None
        state_path = path.with_name('ai_assistance.json')
        state = read_ai_assistance_status(scheduler_settings_path=path)
        previous = state.get('dispatch') or {}
        if previous.get('dispatch_id'):
            if state.get('ownership_check_status') == 'failed':
                # A batch of different unknown items must not repeatedly query
                # the same broken transport or presume its owner exited.
                # Reconnecting is an explicit refresh operation; ordinary
                # requests remain suppressed while ownership is unverified.
                return None
            # An unreadable dispatch is not proof its owner exited: fail closed.
            state = refresh_ai_assistance_status(scheduler_settings_path=path)
            if state.get('agent_status') in {'starting', 'running'}:
                return None
            if previous.get('provider') == 'claude' and state.get('agent_status') == 'failed':
                return None  # explicit same-session recovery, never timer replay
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
            recovery_constraints = (
                '本任务由工程异常触发。使用调度层公共 API 核对运行权；若另一 AI 已接管，'
                '不得抢占或再派生维修 Agent，应记录冲突并退出。',
                '以实际业务验收为完成依据，CC 进程结束或输出分析报告不代表故障解决。',
                '凡修故障接管后进入现场调试研发模式：优先保留原失败现场，读取第一现场日志、'
                '真实画面和 Runtime，在现场定位、修复并验证，不要为方便整单重跑先退回 #34。'
                '若现场已回到 #34 等稳定锚点，先从正式业务入口重新触发原问题点；'
                '无法复现时记录证据，不能凭代码推测宣布修复成功。',
                '修复后由本维修 AI 持有运行权完成真实验收：先验证原失败动作，再从正式 Job '
                '入口整单复跑，检查实际业务结果、资源变化和 next_time；再次重入确认幂等、'
                '无重复领取或消耗。已有真实完成凭证应被正确识别，受限项应形成合法终态。'
                '禁止将工程恢复后的下一轮运行用作尚未完成的验收。只有上述验收通过且 Cell '
                '已结束，才允许交还工程调度并完成 Goal。',
            ) if mode == 'repair' else (
                '本任务是领域研究/离线维护，不是游戏故障接管。不得取得游戏运行权、提交 Cell、'
                '导航、点击、消费、修改调度或热加载 Kernel。工程继续正常运行。',
                '通过领域公共接口保存调查证据与规则；用途不确定时申请人工确认。'
                '调查结论不授予任何消费权限，不能将历史图鉴当作当前库存。',
            )
            dispatch = escalate_to_codex(replace(
                request, business='fanxiu', mode=mode,
                acceptance_checks=(tuple(request.acceptance_checks) +
                    (('fanxiu.engineering_control_returned',) if mode == 'repair' else ())),
                constraints=request.constraints + recovery_constraints))
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
        acceptance_context={'task_id': task_id},
        evidence=(f'Job={task_id}; entry_id={entry_id}',
                  f'终态={terminal!r}', f'原任务={job!r}',
                  '完整第一现场通过 list_scheduler_incidents 与作业日志公共 API 读取。'),
        recovery_instructions=(
            '先核对工程仍持有运行权；已有 AI 接管时退出，不争抢。'
            '仅通过 take_ai_control(entry_id, only_if_idle=True) 在合法空闲时获取运行权；'
            'kernel_busy 或存活 attempt 时等待并继续独立只读调查，禁止改用默认接管、'
            '中断、重启或重提存活作业。pending effect 按原 action_id 回读，不重放。'
            '取得运行权并修复后，从正式 Job 入口提交完整新 attempt。'
            '真实验收通过后 resume_engineering_control() 交还工程。'),
        completion_criteria=('原故障已修复且正式作业达到合法业务终态',
                             'next_time 正确写回，工程调度恢复'),
    )
    # Order: close dispatch and persist this attempt's incident first (one lock),
    # then launch. A leased-action stop of the same attempt may already have
    # closed the gate under this key; the report then claims that incident once
    # and inherits its captured ownership. A repeated report (active or released)
    # neither relaunches nor refreezes. The original terminal is kept unchanged.
    from .kernel_scheduler_control import (
        claim_runtime_error_launch, record_dispatch_hold, runtime_error_hold_key, update_dispatch_hold)
    world = _isolated_world_facts(scheduler_settings_path)
    reported = dict(result)
    try:
        hold = record_dispatch_hold(
            f'{task_id} 运行错误', str(terminal.get('error') or terminal.get('message') or 'error')[:300],
            key=runtime_error_hold_key(task_id, task.get('attempt_id')),
            details={'kind': 'runtime_error', 'task_id': task_id, 'attempt_id': task.get('attempt_id'),
                     'error_type': terminal.get('error_type'), 'launch_status': 'pending'},
            scheduler_settings_path=scheduler_settings_path, world_facts_path=world,
            read_identity=scheduler_settings_path is None)
    except Exception as exc:  # noqa: BLE001 - never launch without a durable hold
        return {**reported, 'dispatch_hold_error': f'{type(exc).__name__}: {exc}'}
    reported['dispatch_hold'] = hold['key']
    if not hold.get('created'):
        joined = claim_runtime_error_launch(
            hold['key'], {'error_type': terminal.get('error_type'),
                          'terminal_error': str(terminal.get('error') or terminal.get('message') or 'error')[:300]},
            world_facts_path=world, scheduler_settings_path=scheduler_settings_path)
        if joined is None:
            return {**reported, 'dispatch_hold_existing': hold.get('status')}
        hold = joined
    if hold.get('engineering_origin') is not True:
        update_dispatch_hold(hold['key'], {'launch_status': 'not_engineering_origin'},
                             world_facts_path=world, scheduler_settings_path=scheduler_settings_path)
        return reported
    try:
        dispatch = request_ai_assistance(request, scheduler_settings_path=scheduler_settings_path,
                                         incident_hold_key=hold['key'])
        launch = ({'launch_status': 'launched', 'dispatch_id': dispatch.dispatch_id} if dispatch
                  else {'launch_status': 'suppressed'})
        if dispatch:
            reported['ai_assistance_dispatch'] = dispatch.model_dump()
    except Exception as exc:
        launch = {'launch_status': 'dispatch_failed', 'launch_error': f'{type(exc).__name__}: {exc}'}
        reported['ai_assistance_error'] = launch['launch_error']
    update_dispatch_hold(hold['key'], launch, world_facts_path=world, scheduler_settings_path=scheduler_settings_path)
    return reported
