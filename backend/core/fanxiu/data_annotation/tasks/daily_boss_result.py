"""Keep business reasons independent of the navigation status projection."""
import re
import time
import math
from copy import deepcopy


def _target_key(task_id, attempt_id):
    if task_id != 'daily-boss' or not isinstance(attempt_id, str) or not re.fullmatch(r'[a-f0-9]{32}', attempt_id):
        raise ValueError('首领目标留证需要精确正式任务/attempt身份')
    return 'fanxiu_daily_boss_target_observations:' + attempt_id


def read_daily_boss_target_observations(task_id: str, attempt_id: str, *, store=None) -> dict:
    """Read exact-attempt target diagnostics, without loading game state."""
    from backend.core.messaging.codex_results import result_store
    key = _target_key(task_id, attempt_id)
    value = (store or result_store()).get_meta(key)
    if value is None:
        return {'status': 'missing', 'task_id': task_id, 'attempt_id': attempt_id}
    if (not isinstance(value, dict) or value.get('task_id') != task_id
            or value.get('attempt_id') != attempt_id or value.get('version') != 1
            or not isinstance(value.get('observations'), list)
            or not 1 <= len(value['observations']) <= 64
            or any(not isinstance(row, dict) or row.get('phase') != 'detail_entry'
                   or not isinstance(row.get('snapshot'), dict)
                   or type(row.get('recorded_at')) not in (int, float)
                   or not math.isfinite(row['recorded_at']) or row['recorded_at'] <= 0
                   for row in value.get('observations', []))
            or value.get('business_complete') is not False
            or value.get('reward_credit_verified') is not False):
        return {'status': 'invalid', 'task_id': task_id, 'attempt_id': attempt_id}
    return dict(value, status='recorded')


def record_daily_boss_target_observation(snapshot: dict, *, store=None):
    """Append an existing native observation under the current formal owner.

    Detail entry can still end in CD skip. This does not claim that a challenge
    was attempted, a target killed, quota consumed or any reward received.
    """
    from backend.core.messaging.codex_results import result_store
    from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
    identity = current_job_log_context()
    if identity.get('task_id') != 'daily-boss' or not identity.get('attempt_id'):
        return None
    if not isinstance(snapshot, dict):
        raise TypeError('首领目标观察必须是结构化字典')
    target_store = store or result_store()
    value = read_daily_boss_target_observations(**identity, store=target_store)
    if value['status'] == 'invalid':
        raise ValueError('原目标留证身份或契约不符')
    observations = list(value.get('observations', []))
    if len(observations) >= 64:
        raise ValueError('当前attempt目标留证达到64次上限')
    observations.append({'phase': 'detail_entry', 'recorded_at': time.time(),
                         'snapshot': deepcopy(snapshot)})
    key = _target_key(**identity)
    target_store.set_meta(key, dict(version=1, **identity, observations=observations,
                                   business_complete=False, reward_credit_verified=False))
    return key


DAILY_BOSS_DEAD_TARGET_MAX_REFRESH_SECONDS = 1800


def daily_boss_dead_target_refresh_seconds(observation, *, now_epoch: float) -> int | None:
    """Seconds until a verified dead detail target refreshes; else ``None``.

    Only a process-verified panel target with ``isDead`` and a future
    ``endTime`` (ms) qualifies. Challenging it lands on the map's refresh
    countdown without combat or credit. The result only defers a challenge;
    it never authorizes a click, a kill or a reward claim. Unknown stays None.
    """
    if (not isinstance(observation, dict) or observation.get('available') is not True
            or observation.get('panel_target_identity_verified') is not True):
        return None
    target = observation.get('target')
    if not isinstance(target, dict) or target.get('isDead') is not True:
        return None
    end_ms = target.get('endTime')
    if type(end_ms) not in (int, float) or not math.isfinite(end_ms):
        return None
    seconds = math.ceil(end_ms / 1000.0 - float(now_epoch))
    if not 0 < seconds <= DAILY_BOSS_DEAD_TARGET_MAX_REFRESH_SECONDS:
        return None
    return seconds


DAILY_BOSS_COMBAT_MAX_WAIT_SECONDS = 1200  # within the 1800s Job budget incl. navigation and list wait


def daily_boss_challenged_target_alive(target_observation, snapshot) -> bool | None:
    """Whether the exact challenged unit is alive in a fresh native snapshot.

    Matches the verified detail target's unitId/group/map within the same game
    process. ``None`` means unknown and never extends a wait; ``False`` is not
    proof of kill credit, only that staying can no longer earn it.
    """
    if (not isinstance(target_observation, dict)
            or target_observation.get('panel_target_identity_verified') is not True
            or not isinstance(target_observation.get('target'), dict)
            or not isinstance(snapshot, dict) or snapshot.get('complete') is not True):
        return None
    expected = target_observation['target']
    process = target_observation.get('evidence') or {}
    if not process.get('pid') or process != {k: (snapshot.get('evidence') or {}).get(k) for k in process}:
        return None
    matches = [row for row in snapshot.get('bosses') or []
               if isinstance(row, dict) and all(row.get(key) == expected.get(key)
                                                for key in ('unitId', 'bossGroupId', 'mapId'))]
    if len(matches) != 1 or type(matches[0].get('isDead')) is not bool:
        return None
    return not matches[0]['isDead']


def daily_boss_world_return_status(status: dict, *, business_message: str,
                                   navigation_result: str) -> dict:
    """Restore a reason only after successful navigation, retaining scene/phase.

    Navigation success is not quota exhaustion. Failed/skipped cleanup retains
    its actual status; an absent reason cannot overwrite a useful diagnostic.
    """
    result = dict(status)
    if navigation_result == 'success' and isinstance(business_message, str) and business_message.strip():
        result.update(message=business_message, business_message=business_message)
    return result
