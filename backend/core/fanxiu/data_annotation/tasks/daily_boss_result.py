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
