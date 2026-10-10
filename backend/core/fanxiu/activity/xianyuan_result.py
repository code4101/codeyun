"""Pure result evidence projection; OCR battle controls never prove daily done.

The caller obtains both snapshots from the public daily-task Runtime provider.
This projection does not read the game, click, persist or originate evidence.
Runtime task 1008's provider-computed done includes its finished-task membership;
OCR is diagnostic only, even when it contains a plausible friendship result.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone


def project_daily_xianyuan_result_evidence(text: str, *, before: dict | None,
        after: dict | None, read_started_at: float) -> dict:
    """Require fresh, same-process, same-reset-day native task 1008 completion.

    Missing/foreign/stale/partial Runtime is unverified. Timestamp precision is
    seconds in the provider, so freshness uses the beginning of that second.
    The parser intentionally grants no result-popup dismissal coordinates.
    """
    normalized = re.sub(r'\s+', '', text or '')
    result = {'business_completion_verified': False, 'criterion': 'unverified',
        'generic_leave_observed': '离开' in normalized,
        'friendship_result_candidate': bool(re.search(r'友好度(?:减少|降低|下降)', normalized)),
        'result_popup_click_authorized': False}

    def reject(reason):
        return {**result, 'reason': reason}

    for snapshot in (before, after):
        if (not isinstance(snapshot, dict) or snapshot.get('task_id') != 1008
            or any(snapshot.get(flag) is not True for flag in ('ok', 'available', 'complete'))
            or snapshot.get('source') != 'runtime_memory'
            or snapshot.get('protocol') != 'QuestMgr.Model.QuestData.taskInfoMap[1]'):
            return reject('native_task_snapshot_unavailable')
    if type(read_started_at) not in {int, float} or not math.isfinite(read_started_at):
        return reject('fresh_observation_time_unknown')
    identities = []
    for snapshot in (before, after):
        evidence = snapshot.get('evidence') or {}
        pair = (evidence.get('pid'), evidence.get('process_start_ticks'))
        if any(type(value) is not int or value <= 0 for value in pair):
            return reject('process_identity_unknown')
        identities.append(pair)
    if identities[0] != identities[1]:
        return reject('process_identity_changed')
    try:
        captured = [datetime.fromisoformat(snapshot['captured_at']) for snapshot in (before, after)]
        if any(moment.tzinfo is None for moment in captured):
            return reject('capture_timezone_unknown')
        if (captured[1].timestamp() < math.floor(read_started_at)
            or captured[1].timestamp() < captured[0].timestamp()):
            return reject('runtime_observation_stale')
        game_zone = timezone(timedelta(hours=8))
        if (captured[0].astimezone(game_zone) - timedelta(hours=5)).date() != (
            captured[1].astimezone(game_zone) - timedelta(hours=5)).date():
            return reject('daily_reset_boundary_changed')
    except (KeyError, TypeError, ValueError, OverflowError):
        return reject('capture_time_unknown')
    if after.get('done') is not True:
        return reject('native_daily_task_not_done')
    return {**result, 'business_completion_verified': True, 'criterion': 'native_task_1008_done',
        'reason': None, 'process_identity': list(identities[1]),
        'turn': after.get('turn'), 'target_turn': after.get('target_turn'),
        'captured_at': after['captured_at']}
