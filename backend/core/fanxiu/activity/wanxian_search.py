"""Bounded list traversal and formal-attempt diagnostics, never completion proof."""
from __future__ import annotations

import hashlib
import time


def wanxian_semantic_unchanged(previous: list[str] | None, current: list[str],
                               confirmations: int) -> int:
    """Two equal nonempty OCR pages are an endpoint hint; empty OCR is unknown.

    The task applies this only after an actual scroll and still searches the
    current page before turning. Animated cards must not keep a bottom cursor
    moving according to a whole-image signature forever.
    """
    before = {''.join(str(x).split()) for x in (previous or []) if str(x).strip()}
    after = {''.join(str(x).split()) for x in current if str(x).strip()}
    return max(0, confirmations) + 1 if before and after and before == after else 0


def wanxian_search_directions(primary: str) -> tuple[str, str]:
    """Search both halves of a list whose inherited cursor is unknown."""
    direction = str(primary).strip().lower()
    if direction not in {"up", "down"}:
        raise ValueError("万仙日常任务列表必须声明垂直加载方向")
    return direction, {"up": "down", "down": "up"}[direction]


def wanxian_search_direction_finished(*, scroll_index: int, limit: int,
                                     previous_scroll_changed: bool | None) -> bool:
    """Call AFTER searching the current frame, including an unchanged scroll's result.

    Unchanged is a traversal hint, not evidence that the activity is absent.
    A direction ending still requires searching the opposite half of the list.
    """
    return scroll_index >= max(0, min(30, int(limit))) or previous_scroll_changed is False


def record_wanxian_search_evidence(observations: list[dict], *, outcome: str,
                                 frame_paths: list[str | None]) -> str | None:
    """Persist caller observations under exact formal ownership; no game reads.

    This receipt does not advance the weekly trigger or authorize clicks/replays.
    Missing formal ownership produces no artifact rather than an invented attempt.
    """
    from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
    from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
    from backend.core.temp_paths import codeyun_temp_root
    identity = current_job_log_context()
    if not identity.get("task_id") or not identity.get("attempt_id"):
        return None
    key = hashlib.sha256((identity['task_id'] + '\0' + identity['attempt_id']).encode()).hexdigest()
    path = codeyun_temp_root('wanxian-search-evidence') / (key + '.json')
    write_data_annotation_json(path, dict(version=1, **identity, recorded_at=time.time(),
        outcome=outcome, business_complete=False, observations=observations,
        frame_paths=frame_paths))
    return str(path)
