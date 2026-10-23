"""Public incident dispatch, observation and explicit same-provider recovery."""
from backend.core.agents.contracts import AgentTaskRequest
from backend.core.agents.policy import resolve


def dispatch_agent_task(request: AgentTaskRequest, **kwargs):
    """Resolve one owner; Fanxiu never falls back to another model provider."""
    plan = resolve(business=request.business, pair=request.pair)
    if request.approval == 'human' and plan['main'] != 'cc':
        raise ValueError('人工审批（approval=human）仅支持本机 cc 主 agent')
    if request.business == 'attendance' and (plan['main'] != 'cc' or plan['aux']):
        raise ValueError('考勤 AI 处理必须使用 cc 单 agent')
    if request.business == 'fanxiu' and (plan['main'] not in {'codex', 'cc'} or plan['aux']):
        raise ValueError('凡修 AI 处理必须使用 codex 或 cc 单 agent')
    if request.business == 'fanxiu' and plan['main'] == 'codex':
        from dataclasses import replace
        from .contracts import required_acceptance_checks
        from .background import dispatch_codex
        request = replace(request, acceptance_checks=required_acceptance_checks(
            request.business, request.mode, request.acceptance_checks))
        return dispatch_codex(request, **kwargs)
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


def resume_agent_dispatch(dispatch_id: str, prompt: str, *, mode: str | None = None,
                          approval: str | None = None,
                          approval_required_tools: tuple[str, ...] | None = None):
    """Resume the same provider/session; ``mode`` explicitly changes the next generation's mode.

    ``mode=None`` keeps the recorded mode. A change is only applied to durable
    local records after terminal/unaccepted/same-session gates; any
    refusal leaves request, generation and prompts untouched. ``approval``
    explicitly opts the next generation into (or out of) human approval;
    ``None`` keeps the recorded transport.
    """
    from .background import incident_root, resume_claude
    if (incident_root(dispatch_id) / 'record.json').is_file():
        return resume_claude(dispatch_id, prompt, mode=mode, approval=approval,
                            approval_required_tools=approval_required_tools)
    if mode is not None or approval is not None or approval_required_tools is not None:
        raise ValueError('Mode/approval transition requires a durable local incident record')
    from backend.core.codex.escalation import resume_codex_dispatch
    return resume_codex_dispatch(dispatch_id, prompt)


def recover_stalled_agent_dispatch(dispatch_id: str, *, expected_generation: int, reason: str):
    """Reap a local wrapper only after its registered CLI is proven dead.

    Does not replay tools, resume a session, or modify business receipts.
    A live CLI or changed generation refuses recovery.
    """
    from .background import incident_root, recover_stalled_claude
    if not (incident_root(dispatch_id) / 'record.json').is_file():
        raise ValueError('Stalled-worker recovery requires a durable local incident record')
    return recover_stalled_claude(dispatch_id, expected_generation=expected_generation, reason=reason)


def cancel_agent_dispatch(dispatch_id: str, *, expected_generation: int, reason: str):
    """Stop exactly this generation's live CLI tree and worker; repeat on terminal is a no-op.

    Preserves files, receipts and session. Never resumes or replaces the actor.
    Changed generation or unconfirmed process identity refuses.
    """
    from .background import cancel_claude, incident_root
    if not (incident_root(dispatch_id) / 'record.json').is_file():
        raise ValueError('Cancellation requires a durable local incident record')
    return cancel_claude(dispatch_id, expected_generation=expected_generation, reason=reason)


def list_agent_permission_requests(dispatch_id: str, *, pending_only: bool = True):
    """Human-approval requests of a durable CC incident (approval=human)."""
    from .approvals import list_permission_requests
    return list_permission_requests(dispatch_id, pending_only=pending_only)


def decide_agent_permission_request(dispatch_id: str, request_id: str, *, generation: int, tool_use_id: str,
                                    input_sha256: str, decision: str, decided_by: str, message: str = '',
                                    interrupt: bool = False):
    """Relay one real person's allow/deny for exactly one pending tool call.

    Callers must not synthesize human approvals. A coordinator may explicitly
    deny an out-of-scope request under its own identity. Stale, expired,
    cross-generation or repeated answers are refused; unanswered requests deny.
    """
    from .approvals import decide_permission_request
    return decide_permission_request(dispatch_id, request_id, generation=generation, tool_use_id=tool_use_id,
                                     input_sha256=input_sha256, decision=decision, decided_by=decided_by,
                                     message=message, interrupt=interrupt)
