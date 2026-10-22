"""Transport-neutral engineering task and dispatch receipts.

Legacy Codex names (CodexEscalationRequest/CodexDispatch/CodexDispatchStatus)
remain aliases or adapters; callers should use these provider-neutral types.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DispatchState = Literal["starting", "running", "completed", "failed"]
INCIDENT_MODES = ("repair", "read_only_research", "maintenance", "smoke")
# cli_policy: headless ``claude -p``; prompts not pre-approved become denials.
# human: explicit opt-in (CC only); such prompts wait for one bound human decision.
APPROVAL_MODES = ("cli_policy", "human")


def required_acceptance_checks(business: str, mode: str, checks=()) -> tuple[str, ...]:
    """Mode-mandated engineering verifiers, kept in order and deduplicated.

    A Fanxiu repair may take runtime ownership, so it always also requires the
    engineering-control-returned verifier — at dispatch and on a mode change.
    """
    checks = tuple(checks or ())
    if business == "fanxiu" and mode == "repair":
        checks = tuple(dict.fromkeys((*checks, "fanxiu.engineering_control_returned")))
    return checks


@dataclass(frozen=True)
class AgentTaskRequest:
    """One incident owned by the configured main agent until business acceptance."""
    title: str
    problem: str
    objective: str
    suggested_focus: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    attempted_actions: tuple[str, ...] = ()
    recovery_instructions: str = ""
    completion_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    business: str = ""
    pair: str = ""  # Task-local override, never changes the business default.
    # repair: may take runtime ownership; read_only_research: never; smoke: framework check.
    mode: str = "repair"
    # Registered engineering verifiers (backend.core.agents.acceptance) that must
    # pass, in addition to the agent's structured receipt, before "completed".
    acceptance_checks: tuple[str, ...] = ()
    acceptance_context: dict[str, Any] = field(default_factory=dict)
    execution_timeout_seconds: int = 3600
    approval: str = "cli_policy"
    # Unanswered human approval requests are denied after this many seconds.
    approval_timeout_seconds: int = 1800
    # Explicit opt-in: these built-in tools must request a person each time.
    # Adds process-local ask rules; never changes saved permissions or deny rules.
    approval_required_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentDispatch:
    """Receipt for one background incident; returned immediately after launch."""
    dispatch_id: str
    provider: str  # "claude" | "codex"
    resource: str  # "cc" | "codex"
    pid: int
    workspace_dir: str
    request_path: str
    prompt_path: str
    stdout_path: str
    stderr_path: str
    created_at: str
    session_id: str | None = None
    mode: str = "repair"
    transport: str = "cli"

    @property
    def thread_id(self) -> str | None:  # Legacy CodexDispatch field name.
        return self.session_id

    def model_dump(self) -> dict[str, Any]:
        return {**asdict(self), "thread_id": self.session_id}


@dataclass(frozen=True)
class AgentDispatchStatus:
    """Observable state; "completed" means accepted, never merely "process exited".

    ``acceptance`` separates transport completion from business acceptance:
    accepted / pending / blocked / missing_receipt / receipt_incomplete /
    verifier_failed / transport_failed. Unaccepted dispatches are "failed" and
    carry a resumable diagnostic in ``error``.
    """
    dispatch_id: str
    status: DispatchState
    provider: str
    pid: int
    session_id: str | None
    session_url: str | None
    last_message: str | None
    error: str | None
    stdout_path: str
    stderr_path: str
    acceptance: str | None = None
    goal_status: str | None = None
    progress_diagnostics: dict[str, Any] | None = field(default=None)

    @property
    def thread_id(self) -> str | None:
        return self.session_id

    @property
    def codex_url(self) -> str | None:
        return self.session_url if self.provider == "codex" else None

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)
