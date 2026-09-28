"""榜单未兑现义务的 AI 升级：能力缺口立即上报，同一技术失败两次上报。

投递不是接管；不改变工程运行权。持久化问题指纹与投递状态，避免每十分钟
生成一个重复 Agent。已有 AI 所有者时将异常交回当前所有者。
"""
import hashlib
import json
import time
import traceback

from backend.core.codex import CodexEscalationRequest
from .ai_assistance import request_ai_assistance
from backend.core.codex.escalation import inspect_codex_dispatch
from backend.core.temp_paths import codeyun_temp_root
from backend.core.fanxiu.activity.ranking_lifecycle_store import (
    ranking_checkpoint_evidence, record_ranking_checkpoint_evidence,
)
from .kernel_scheduler_control import read_scheduler_settings


class RankingCapabilityMissing(RuntimeError):
    """A due exchange obligation has no accepted executor; never a success."""


def report_ranking_failure(session, *, checkpoint, occurrence, error,
                           task_id, entry_id, frame_data_url=None):
    """Persist first evidence and deduplicate an allowlisted repair dispatch."""
    evidence = ranking_checkpoint_evidence(session, checkpoint)
    previous = dict(evidence.get('ai_repair') or {})
    signature = hashlib.sha256(f'{type(error).__name__}:{error}'.encode()).hexdigest()
    repeated = previous.get('signature') == signature
    failures = int(previous.get('failures') or 0)+1 if repeated else 1
    state = {**(previous if repeated else {}), 'signature': signature, 'failures': failures,
             'error_type': type(error).__name__, 'error': str(error)}
    def save():
        evidence['ai_repair'] = state
        record_ranking_checkpoint_evidence(session, checkpoint, evidence=evidence)

    identity = hashlib.sha256(repr(checkpoint.key).encode()).hexdigest()[:20]
    root = codeyun_temp_root('fanxiu-ranking-incidents', identity)
    path = root / f'{signature[:20]}.json'
    if not path.exists():
        incident = dict(checkpoint=checkpoint.as_dict(), occurrence=repr(occurrence),
                        error_type=type(error).__name__, error=str(error),
                        traceback=''.join(traceback.format_exception(error)), task_id=task_id)
        if frame_data_url and str(frame_data_url).startswith('data:image/'):
            import base64
            frame = path.with_suffix('.png')
            frame.write_bytes(base64.b64decode(frame_data_url.split(',', 1)[1]))
            incident['frame_path'] = str(frame)
        path.write_text(json.dumps(incident, ensure_ascii=False, indent=2), encoding='utf-8')
    state.update(evidence_path=str(path))

    if not isinstance(error, RankingCapabilityMissing) and failures < 2:
        state['status'] = 'retry_once'
        save()
        return state
    if not read_scheduler_settings().get('job_group_enabled', True):
        state['status'] = 'current_ai_owner'
        save()
        return state
    # Scene recovery may already have dispatched this very failure.
    existing = getattr(error, 'codex_dispatch_id', None)
    if existing:
        state.update(status='dispatched', dispatch_id=existing)
        save()
        return state
    if repeated and state.get('dispatch_id'):
        try:
            dispatch_status = inspect_codex_dispatch(state['dispatch_id'])
            state['dispatch_status'] = dispatch_status.status
            state['dispatch_error'] = dispatch_status.error
            if dispatch_status.status in ('starting', 'running'):
                save()
                return state
        except Exception as exc:
            state['dispatch_error'] = str(exc)
    # Failed delivery/finished but unresolved agents are visible and bounded;
    # inspect and redeliver after 30 minutes instead of flooding or silencing.
    if repeated and time.time()-float(state.get('requested_at') or 0) < 1800:
        save()
        return state
    state['requested_at'] = time.time()
    request = CodexEscalationRequest(
        title=f'凡修榜单义务未完成：{checkpoint.activity_type}/{checkpoint.checkpoint_kind}',
        problem=f'{type(error).__name__}: {error}；同一问题出现 {failures} 次，不能把未完成义务当作成功或无限重试。',
        objective='修复真实业务及其调度、接口或资产根因，完成正式幂等验收并恢复凡修工程运行。',
        evidence=(f'第一现场：{path}', f'实例：{occurrence!r}', f'任务：{task_id}，入口：{entry_id}'),
        attempted_actions=('已检查当前实例与到期 checkpoint；完成键不存在。',
                           '能力缺口立即升级；普通技术故障已允许一次工程重试。'),
        recovery_instructions=f'先用 take_ai_control 取得运行权；修复后从 run_now_scheduler_task 的 {task_id} 当前时间入口提交完整新 attempt。',
        completion_criteria=('实际目标完成且有最新游戏证据；若窗口已经关闭，明确记录无法补救，禁止伪造完成。',
                             '再次运行不重复消耗；next_time 正确写回；恢复工程调度。'),
    )
    try:
        dispatch = request_ai_assistance(request)
        if dispatch is None:
            state['status'] = 'assistance_suppressed'
            save()
            return state
        state.update(status='dispatched', dispatch_id=dispatch.dispatch_id,
                     request_path=dispatch.request_path)
    except Exception as exc:
        state.update(status='dispatch_failed', dispatch_error=f'{type(exc).__name__}: {exc}')
    save()
    return state
