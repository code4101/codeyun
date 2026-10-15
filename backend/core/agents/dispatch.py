"""Public incident dispatch, observation and explicit same-provider recovery."""
from backend.core.agents.contracts import AgentTaskRequest
from backend.core.agents.policy import resolve


def dispatch_agent_task(request: AgentTaskRequest, **kwargs):
    """Resolve one owner; Fanxiu never falls back to another model provider."""
    plan = resolve(business=request.business, pair=request.pair)
    if request.business in {'fanxiu', 'attendance'} and (plan['main'] != 'cc' or plan['aux']):
        raise ValueError('凡修和考勤 AI 处理必须使用 cc 单 agent')
    if plan['main'] == 'cc':
        from dataclasses import replace
        from backend.core.agents.contracts import required_acceptance_checks
        request = replace(request, acceptance_checks=required_acceptance_checks(
            request.business, request.mode, request.acceptance_checks))
        from .background import dispatch_claude
        return dispatch_claude(request, **kwargs)
    if plan["main"] != "codex":
        raise ValueError('该资源未接入后台事故执行器')
    from backend.core.codex import escalate_to_codex
    return escalate_to_codex(request, **kwargs)


def inspect_agent_dispatch(dispatch_id: str):
    """Legacy records keep their provider; changing defaults cannot reassign one."""
    from .background import incident_root, inspect_claude
    if (incident_root(dispatch_id) / 'record.json').is_file():
        return inspect_claude(dispatch_id)
    from backend.core.codex.escalation import inspect_codex_dispatch
    return inspect_codex_dispatch(dispatch_id)


def resume_agent_dispatch(dispatch_id: str, prompt: str, *, mode: str | None = None):
    """Resume the same provider/session; ``mode`` explicitly changes the next generation's mode.

    ``mode=None`` keeps the recorded mode. A change is only applied by the
    Claude provider after its own terminal/unaccepted/same-session gates; any
    refusal leaves request, generation and prompts untouched.
    """
    from .background import incident_root, resume_claude
    if (incident_root(dispatch_id) / 'record.json').is_file():
        return resume_claude(dispatch_id, prompt, mode=mode)
    if mode is not None:
        raise ValueError('Mode transition is only supported for Claude incident dispatches')
    from backend.core.codex.escalation import resume_codex_dispatch
    return resume_codex_dispatch(dispatch_id, prompt)


def recover_stalled_agent_dispatch(dispatch_id: str, *, expected_generation: int, reason: str):
    """Reap a Claude wrapper only after its registered CLI is proven dead.

    Does not replay tools, resume a session, or modify business receipts.
    A live CLI or changed generation refuses recovery.
    """
    from .background import incident_root, recover_stalled_claude
    if not (incident_root(dispatch_id) / 'record.json').is_file():
        raise ValueError('Stalled-worker recovery is only supported for Claude dispatches')
    return recover_stalled_claude(dispatch_id, expected_generation=expected_generation, reason=reason)
