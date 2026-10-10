"""Exact native failure receipts, distinct from transport outcome uncertainty."""
from __future__ import annotations

NATIVE_FAILURE_RECEIPT_CONTRACT = 'exact-native-failed-v1'


class CodexTurnFailed(RuntimeError):
    """A matching turn/completed event explicitly reported status=failed.

    Preserve bounded official message/codexErrorInfo/additionalDetails rather
    than trying to recover them from Python's string representation. This is
    terminal evidence only: it neither releases a writer nor admits a retry.
    """

    def __init__(self, thread_id: str, turn_id: str, error: dict | None,
                 error_events: list[dict] | None = None):
        from backend.core.codex.research_supervision import project_codex_turn_error
        if not thread_id or not turn_id:
            raise ValueError('Exact failed thread and turn identity required')
        self.thread_id, self.turn_id = thread_id, turn_id
        self.native_terminal_status = 'failed'
        self.native_error = project_codex_turn_error(error) if isinstance(error, dict) else {}
        self.error_events = [project_codex_turn_error(row) for row in (error_events or [])[-8:]
                             if isinstance(row, dict)]
        super().__init__(f'Codex turn failed: {self.native_error}')
