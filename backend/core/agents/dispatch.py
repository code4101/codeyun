"""Dispatch an engineering task through its main-agent transport adapter."""
from backend.core.agents.contracts import AgentTaskRequest
from backend.core.agents.policy import resolve


def dispatch_agent_task(request: AgentTaskRequest, **kwargs):
    """Codex owns background incidents; auxiliaries are selected in the task prompt.

    CLI CC/DS tasks use run_agent for bounded foreground execution. This public
    background boundary preserves the existing Codex goal/status lifecycle.
    """
    plan = resolve(business=request.business, pair=request.pair)
    if plan["main"] != "codex":
        raise ValueError("后台事故目前只接入 Codex 主 agent；CC/DS 使用 run_agent")
    from backend.core.codex import escalate_to_codex
    return escalate_to_codex(request, **kwargs)
