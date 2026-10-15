"""Public incident dispatch, observation and explicit same-provider recovery."""
from backend.core.agents.contracts import AgentTaskRequest
from backend.core.agents.policy import resolve


def dispatch_agent_task(request: AgentTaskRequest, **kwargs):
    """Resolve one owner; Fanxiu never falls back to another model provider."""
    plan = resolve(business=request.business, pair=request.pair)
    if request.business in {'fanxiu', 'attendance'} and (plan['main'] != 'cc' or plan['aux']):
        raise ValueError('凡修和考勤 AI 处理必须使用 cc 单 agent')
    if plan['main'] == 'cc':
        if request.business == 'fanxiu' and request.mode == 'repair':
            from dataclasses import replace
            request = replace(request, acceptance_checks=tuple(dict.fromkeys(
                (*request.acceptance_checks, 'fanxiu.engineering_control_returned'))))
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


def resume_agent_dispatch(dispatch_id: str, prompt: str):
    from .background import incident_root, resume_claude
    if (incident_root(dispatch_id) / 'record.json').is_file():
        return resume_claude(dispatch_id, prompt)
    from backend.core.codex.escalation import resume_codex_dispatch
    return resume_codex_dispatch(dispatch_id, prompt)
