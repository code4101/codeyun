"""Preserve domain evidence at the formal daily-flow/framework boundary."""
from __future__ import annotations

from typing import Any


def project_daily_task_terminal(flow_result: Any, *, message: str) -> dict:
    """Keep returned evidence without changing framework terminal semantics.

    Only an exception fails the enclosing Scheduler attempt. Domain statuses,
    native claim IDs and navigation observations are evidence, not permissions
    or a resumable cursor. The caller has already enforced the no-next_time
    contract and resolved the original business completion message.
    """
    return {**(flow_result if isinstance(flow_result, dict) else {}),
            "result": "success", "message": message}
