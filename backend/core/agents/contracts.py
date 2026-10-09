"""Transport-neutral engineering task; legacy Codex request is an alias."""
from dataclasses import dataclass


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
