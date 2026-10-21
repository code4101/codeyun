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
from backend.core.agents.dispatch import resume_agent_dispatch
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


def _same_session_incident_prompt(request: CodexEscalationRequest) -> str:
    """New natural incident for an existing, exited owner; no old action is replayed."""
    task_id = (request.acceptance_context or {}).get('task_id')
    lines = [f'新的自然工程故障（同一会话续接；旧事故未验收部分仍有效）：{request.title}',
             f'问题：{request.problem}', f'目标：{request.objective}', '证据：',
             *(f'- {item}' for item in request.evidence)]
    if request.recovery_instructions:
        lines.append(f'恢复入口：{request.recovery_instructions}')
    lines.append('按原事故同一运行权与验收规则处理；已完成的消费/领取不得重放。'
                 + (f'最终 job_verifications 必须同时包含 {task_id} 的新鲜正式终态。' if task_id else ''))
    return '\n'.join(lines)


def request_ai_assistance(
    request: CodexEscalationRequest, *, scheduler_settings_path: Path | None = None,
    mode: str = 'repair', incident_hold_key: str | None = None, decision: dict | None = None,
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
    An optional ``decision`` dict receives the typed outcome: ``reason`` in
    launched/resumed/gate_closed/ownership_unverified/live_owner/resume_cooldown/
    same_title_cooldown/dispatch_failed_cooldown and ``retry_after`` (epoch
    seconds, set only for the three cooldowns; None otherwise).
    """
    decision = {} if decision is None else decision

    def suppressed(reason: str, retry_after: float | None = None) -> None:
        decision.update(reason=reason, retry_after=retry_after)
        return None

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
            return suppressed('gate_closed')  # No configured owner is not an engineering start.
        settings = read_scheduler_settings(scheduler_settings_path=path)
        if not assistance_enabled(settings):
            # A runtime error closes the gate before any launch. Its own durable
            # hold (engineering origin, launch pending) is the only authority to
            # launch with the gate closed; AI-owned failures never qualify.
            from .kernel_scheduler_control import runtime_error_launch_authorized
            if not (settings.get('behavior_tree_enabled', True) and runtime_error_launch_authorized(
                    incident_hold_key, world_facts_path=_isolated_world_facts(scheduler_settings_path))):
                return suppressed('gate_closed')
        state_path = path.with_name('ai_assistance.json')
        state = read_ai_assistance_status(scheduler_settings_path=path)
        previous = state.get('dispatch') or {}
        resume_previous = False
        if previous.get('dispatch_id'):
            if state.get('ownership_check_status') == 'failed':
                # A batch of different unknown items must not repeatedly query
                # the same broken transport or presume its owner exited.
                # Reconnecting is an explicit refresh operation; ordinary
                # requests remain suppressed while ownership is unverified.
                return suppressed('ownership_unverified')
            # An unreadable dispatch is not proof its owner exited: fail closed.
            state = refresh_ai_assistance_status(scheduler_settings_path=path)
            if state.get('agent_status') in {'starting', 'running'}:
                return suppressed('live_owner')
            # An unaccepted Claude owner whose processes all exited is
            # continued in its own session (the provider re-checks the exact
            # worker/child identities, session and acceptance under its locks),
            # never replaced by a second writer or another provider.
            resume_previous = previous.get('provider') == 'claude' and state.get('agent_status') == 'failed'
        now = time.time()
        if resume_previous and now - float(state.get('requested_at') or 0) < 1800:
            # one same-session recovery per 30 minutes, whatever the error source
            return suppressed('resume_cooldown', float(state['requested_at']) + 1800)
        key = hashlib.sha256(request.title.encode('utf-8')).hexdigest()
        recent = {k: v for k, v in (state.get('recent_requests') or {}).items()
                  if now - float(v) < 1800}
        # Alternating error sources cannot bypass the same-incident cooldown.
        if state.get('incident_key') and now - float(state.get('requested_at') or 0) < 1800:
            recent[state['incident_key']] = state['requested_at']
        failed_recently = (state.get('status') == 'dispatch_failed'
                           and now - float(state.get('requested_at') or 0) < 1800)
        if key in recent or failed_recently:
            reason = 'same_title_cooldown' if key in recent else 'dispatch_failed_cooldown'
            until = max(float(recent[key]) if key in recent else 0.0,
                        float(state['requested_at']) if failed_recently else 0.0)
            return suppressed(reason, until + 1800)
        recent[key] = now
        state = dict(incident_key=key, title=request.title, requested_at=now,
                     status='dispatching', resolved=False, recent_requests=recent)
        if resume_previous:
            # A refused/failed resume must leave the same owner to resume later.
            state['dispatch'] = previous
        write_data_annotation_json(state_path, state)
        if resume_previous:
            try:
                dispatch = resume_agent_dispatch(
                    previous['dispatch_id'], _same_session_incident_prompt(request), mode=mode)
            except Exception as exc:
                state.update(status='dispatch_failed', error=f'{type(exc).__name__}: {exc}')
                write_data_annotation_json(state_path, state)
                raise
            state.update(status='dispatched', resumed=True, dispatch=dispatch.model_dump())
            write_data_annotation_json(state_path, state)
            decision.update(reason='resumed', retry_after=None)
            return dispatch
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
        decision.update(reason='launched', retry_after=None)
        return dispatch


def _failed_job_request(task_id: str, entry_id: str, terminal: dict, job: dict, *,
                        hold_key: str) -> CodexEscalationRequest:
    return CodexEscalationRequest(
        title=f"凡修工程作业失败：{task_id}",
        problem=str(terminal.get('error') or terminal.get('message') or '正式作业异常结束'),
        objective='修复该作业的真实故障，正式整单复验，并恢复工程自主运行。',
        acceptance_context={'task_id': task_id},
        evidence=(f'Job={task_id}; entry_id={entry_id}; incident_hold_key={hold_key}',
                  f'终态={terminal!r}', f'原任务={job!r}',
                  '完整第一现场通过 list_scheduler_incidents 与作业日志公共 API 读取。'),
        recovery_instructions=(
            '先核对工程仍持有运行权；已有 AI 接管时退出，不争抢。'
            f'仅通过 take_ai_control(entry_id, only_if_idle=True, incident_hold_key={hold_key!r}) '
            '在合法空闲时获取运行权：工程按该 hold 登记的 dispatch/会话及存活 CLI 进程核验调用者，'
            '仅 ai_control_acquired=True 且 handoff_ready=True 时持权；其余 ai_control_reason 一律退出，不重试他法。'
            'kernel_busy 或存活 attempt 时等待并继续独立只读调查，禁止改用默认接管、'
            '中断、重启或重提存活作业。pending effect 按原 action_id 回读，不重放。'
            '取得运行权并修复后，从正式 Job 入口提交完整新 attempt。'
            '真实验收通过后 resume_engineering_control() 交还工程。'),
        completion_criteria=('原故障已修复且正式作业达到合法业务终态',
                             'next_time 正确写回，工程调度恢复'),
    )


# Hold launch states that keep a durable dispatch intent for the resident retry.
# owner_active/owner_unverified wait for the existing owner to exit or be
# verified; they never authorize a second writer beside a live one.
_OWNER_WAIT_LAUNCH = {'live_owner': 'owner_active', 'ownership_unverified': 'owner_unverified'}
RETRYABLE_LAUNCH_STATUSES = frozenset({'deferred', 'dispatch_failed', *_OWNER_WAIT_LAUNCH.values()})
MAX_DISPATCH_FAILURES = 5


def _dispatch_failure_backoff(failures: int) -> float:
    """Bounded backoff after a transport failure: 5m, 10m, 20m, 40m, then 60m."""
    return float(min(300 * 2 ** max(0, failures - 1), 3600))


def _suppressed_launch(decision: dict) -> dict:
    reason = decision.get('reason')
    if decision.get('retry_after') is not None:
        return {'launch_status': 'deferred', 'deferred_reason': reason,
                'retry_after': float(decision['retry_after'])}
    if reason in _OWNER_WAIT_LAUNCH:
        return {'launch_status': _OWNER_WAIT_LAUNCH[reason], 'deferred_reason': reason, 'retry_after': None}
    return {'launch_status': 'suppressed', 'deferred_reason': reason, 'retry_after': None}


def _failed_launch(hold: dict, exc: Exception, now: float) -> dict:
    failures = int(hold.get('dispatch_failures') or 0) + 1
    exhausted = failures >= MAX_DISPATCH_FAILURES
    return {'launch_status': 'needs_operator' if exhausted else 'dispatch_failed',
            'launch_error': f'{type(exc).__name__}: {exc}', 'dispatch_failures': failures,
            'retry_after': None if exhausted else now + _dispatch_failure_backoff(failures)}


def report_failed_job(*, task: dict, result: dict, entry_id: str,
                      scheduler_settings_path: Path | None = None) -> dict:
    """Escalate a terminal engineering error, without interpreting game success.

    Domain recovery runs first (e.g. bounded login recovery). This fallback
    covers errors without a dedicated scene/ranking hook. An active repair
    launched by those hooks is deduplicated at the same boundary.
    A suppressed launch keeps a durable intent on the hold (``deferred`` with
    retry_after for cooldowns, ``owner_active``/``owner_unverified`` beside an
    existing owner, bounded ``dispatch_failed`` backoff) for
    retry_deferred_runtime_error_launches; nothing is replayed.
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
    from .kernel_scheduler_control import (
        claim_runtime_error_launch, record_dispatch_hold, runtime_error_hold_key, update_dispatch_hold)
    hold_key = runtime_error_hold_key(task_id, task.get('attempt_id'))
    request = _failed_job_request(task_id, entry_id, terminal, job, hold_key=hold_key)
    # Order: close dispatch and persist this attempt's incident first (one lock),
    # then launch. A leased-action stop of the same attempt may already have
    # closed the gate under this key; the report then claims that incident once
    # and inherits its captured ownership. A repeated report (active or released)
    # neither relaunches nor refreezes. The original terminal is kept unchanged.
    world = _isolated_world_facts(scheduler_settings_path)
    reported = dict(result)
    try:
        hold = record_dispatch_hold(
            f'{task_id} 运行错误', str(terminal.get('error') or terminal.get('message') or 'error')[:300],
            key=hold_key,
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
    decision: dict = {}
    try:
        dispatch = request_ai_assistance(request, scheduler_settings_path=scheduler_settings_path,
                                         incident_hold_key=hold['key'], decision=decision)
        launch = ({'launch_status': 'launched', 'dispatch_id': dispatch.dispatch_id} if dispatch
                  else _suppressed_launch(decision))
        if dispatch:
            reported['ai_assistance_dispatch'] = dispatch.model_dump()
    except Exception as exc:
        launch = _failed_launch(hold, exc, time.time())
        reported['ai_assistance_error'] = launch['launch_error']
    launch['request_context'] = {'task_id': task_id, 'entry_id': entry_id, 'terminal': terminal, 'job': job}
    update_dispatch_hold(hold['key'], launch, world_facts_path=world, scheduler_settings_path=scheduler_settings_path)
    return reported


def _execution_activity_blocker(hold: dict, *, now: float, kernel_status: dict | None,
                                scheduler_state_path: Path | None, world: Path | None) -> str | None:
    """Fail closed while any Cell may own the game or another AI recently worked it.

    A closed gate cannot tell an AI that took control after the hold from no
    owner. Cell activity after the hold within the 30-minute AI idle window is
    treated as such an owner; a busy Kernel or running attempt always is.
    """
    from .kernel_scheduler_control import read_scheduler_tasks
    if kernel_status is None:
        from backend.core.fanxiu.behavior_tree.jupyter_kernel import fanxiu_kernel_manager_status
        kernel_status = fanxiu_kernel_manager_status()
    if kernel_status.get('execution_state') == 'busy':
        return 'kernel_busy'
    if any(t.get('last_result') == 'running' and t.get('attempt_id') for t in read_scheduler_tasks(
            scheduler_state_path=scheduler_state_path, world_facts_path=world)):
        return 'attempt_running'
    activity = max(float(kernel_status.get('last_cell_submitted_at') or 0),
                   float(kernel_status.get('last_cell_completed_at') or 0))
    if activity > float(hold.get('held_at_epoch') or 0) and now - activity < 1800:
        return 'recent_cell_activity'
    return None


def retry_deferred_runtime_error_launches(
    *, now: float | None = None, scheduler_settings_path: Path | None = None,
    scheduler_state_path: Path | None = None, kernel_status: dict | None = None,
) -> dict:
    """Resident provider tick: relaunch at most one deferred engineering incident.

    Under the control lock it selects the oldest active engineering-origin
    runtime-error hold whose retryable launch intent is due and, with no live
    Cell/attempt, claims it ``pending`` and calls request_ai_assistance once.
    That call keeps every owner gate: a live owner stays ``owner_active``, an
    exited unaccepted Claude owner is resumed in its exact session, and
    cooldowns stay ``deferred``. Transport failures back off (bounded) and
    become ``needs_operator`` after MAX_DISPATCH_FAILURES. It only creates a
    dispatch intent: no Cell, ADB, take_ai_control or Job next_time/last_result.
    """
    from .kernel_scheduler_control import active_dispatch_holds, dispatch_hold_attention, update_dispatch_hold
    path = _settings_path(scheduler_settings_path)
    world = _isolated_world_facts(scheduler_settings_path)
    with assistance_control_lock(path):
        now = time.time() if now is None else now
        due = sorted(
            ((key, hold) for key, hold in active_dispatch_holds(world).items()
             if hold.get('kind') == 'runtime_error' and hold.get('engineering_origin') is True
             and hold.get('launch_status') in RETRYABLE_LAUNCH_STATUSES and hold.get('request_context')
             and float(hold.get('retry_after') or 0) <= now),
            key=lambda item: float(item[1].get('held_at_epoch') or 0))
        # Holds the selection above can never pick stay publicly visible on
        # every tick, including ticks that dispatch another incident.
        attention = dispatch_hold_attention(world)
        skipped = {'undispatchable_holds': attention} if attention else {}
        if not due:
            return {'dispatched': 0, 'reason': 'no_due_incident', **skipped}
        key, hold = due[0]
        blocker = _execution_activity_blocker(hold, now=now, kernel_status=kernel_status,
                                              scheduler_state_path=scheduler_state_path, world=world)
        if blocker:
            return {'dispatched': 0, 'reason': blocker, 'hold': key, **skipped}
        context = hold['request_context']
        request = _failed_job_request(str(context.get('task_id') or ''), str(context.get('entry_id') or ''),
                                      context.get('terminal') or {}, context.get('job') or {}, hold_key=key)
        # The pending claim is the gate-closed launch authority; it is replaced
        # below within the same lock, so no other reader observes it.
        update_dispatch_hold(key, {'launch_status': 'pending'}, world_facts_path=world,
                             scheduler_settings_path=scheduler_settings_path)
        decision: dict = {}
        try:
            dispatch = request_ai_assistance(request, scheduler_settings_path=scheduler_settings_path,
                                             incident_hold_key=key, decision=decision)
            launch = ({'launch_status': 'launched', 'dispatch_id': dispatch.dispatch_id, 'retry_after': None,
                       'resumed': decision.get('reason') == 'resumed'} if dispatch
                      else _suppressed_launch(decision))
        except Exception as exc:  # noqa: BLE001 - failure stays visible on the hold
            launch = _failed_launch(hold, exc, now)
        launch['last_retry_at'] = now
        update_dispatch_hold(key, launch, world_facts_path=world, scheduler_settings_path=scheduler_settings_path)
        return {'dispatched': int(launch['launch_status'] == 'launched'), 'hold': key, **launch, **skipped}
