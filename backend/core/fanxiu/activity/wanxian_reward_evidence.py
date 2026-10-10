"""Formal-attempt reward observations, without inferred native tier counts."""
import hashlib
import json
import re
import time
from pathlib import Path


def _receipt_path(task_id, attempt_id, phase, directory):
    from backend.core.temp_paths import codeyun_temp_root
    if task_id != 'weekly-wanxian' or not isinstance(attempt_id, str) or not re.fullmatch(r'[a-f0-9]{32}', attempt_id):
        raise ValueError('万仙留证需要精确的正式任务和attempt身份')
    key = hashlib.sha256((task_id + '\0' + attempt_id).encode()).hexdigest()
    root = Path(directory) if directory is not None else codeyun_temp_root('wanxian-reward-evidence', create=False)
    return root / (key + '-' + phase + '.json')


def record_wanxian_reward_observation(phase: str, *, runtime: dict,
                                     frame_path: str | None, ocr_result: dict | None = None,
                                     directory=None):
    """Record each side independently so a failed claim retains its before frame.

    QuestMgr is a shared task container. Its rows alone do not identify the
    Wanxian panel or reset cycle; this receipt intentionally does not project a
    claimed count. A future domain binding must prove that identity first.
    No game read/write, notification or scheduler operation occurs here.
    """
    from backend.core.fanxiu.data_annotation.subtask_execution import current_job_log_context
    from backend.core.fanxiu.data_annotation.state import write_data_annotation_json
    if phase not in {'before', 'after'}:
        raise ValueError('领奖观察阶段必须是before或after')
    identity = current_job_log_context()
    if identity.get('task_id') != 'weekly-wanxian' or not identity.get('attempt_id'):
        return None
    path = _receipt_path(identity['task_id'], identity['attempt_id'], phase, directory)
    write_data_annotation_json(path, {
        'version': 1, **identity, 'phase': phase, 'recorded_at': time.time(),
        'runtime': runtime, 'frame_path': frame_path, 'ocr_result': ocr_result,
        'native_activity_identity_verified': False, 'native_cycle_identity_verified': False,
        'native_claimed_tier_count': None, 'business_complete': False})
    return str(path)


def read_wanxian_reward_observations(task_id: str, attempt_id: str, *, directory=None) -> dict:
    """Read only this exact attempt's two phases; no scan, backfill or game read.

    A before-only receipt is partial, preserving a failure's first scene.
    Corrupt/foreign receipt data invalidates the pair instead of confirming
    rewards. Even two valid phases retain unknown native tier counts until a
    separate domain/cycle binding exists. This read never creates directories.
    """
    paths = {phase: _receipt_path(task_id, attempt_id, phase, directory)
             for phase in ('before', 'after')}
    observations = {}
    invalid = []
    for phase, path in paths.items():
        if not path.exists():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict) or any((
                value.get('version') != 1, value.get('task_id') != task_id,
                value.get('attempt_id') != attempt_id, value.get('phase') != phase,
                not isinstance(value.get('runtime'), dict),
                value.get('native_claimed_tier_count') is not None,
                value.get('business_complete') is not False,
                value.get('native_activity_identity_verified') is not False,
                value.get('native_cycle_identity_verified') is not False,
            )):
                raise ValueError('receipt_identity_or_contract_mismatch')
            observations[phase] = value
        except (ValueError, UnicodeError, OSError):
            invalid.append(phase)
    return {'task_id': task_id, 'attempt_id': attempt_id,
            'status': 'invalid' if invalid else 'recorded' if len(observations) == 2 else 'partial' if observations else 'missing',
            'observations': observations, 'invalid_phases': invalid,
            'native_claimed_tier_count': None, 'business_complete': False}
