"""Durable intents for the verified 太清本源 score-consumption component."""
import json
import os
from uuid import uuid4
from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path(activity_id):
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('Expected positive activity ID')
    folder = fanxiu_data_annotation_dir() / 'dandao-score-receipts'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{activity_id}.json'


def _write(path, rows):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_dandao_score_receipts(activity_id: int) -> list[dict]:
    path = _path(activity_id)
    with FileLock(str(path) + '.lock', timeout=5):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def arm_dandao_score_batch(activity_id: int, before: dict, count: int) -> str:
    """Called immediately before the sole Start click, after quantity checks."""
    before = {**before, 'materials': {str(k): v for k, v in before.get('materials', {}).items()}}
    if (type(count) is not int or count <= 0 or type(before.get('score')) is not int
            or before['score'] < 0 or before.get('recipe_id') != 711070
            or before.get('formula') != 'AnyItem|12_9031021_9031022'
            or set(before['materials']) != {'9031021', '9031022'}
            or any(type(v) is not int or v < 0 for v in before['materials'].values())
            or sum(before['materials'].values()) < count * 12
            or not before.get('evidence')):
        raise ValueError('丹道积分消费事实不完整')
    if before.get('final_threshold') is not None:
        ids = before.get('task_ids') or []
        if (type(before['final_threshold']) is not int or before['final_threshold'] <= before['score']
                or not ids or len(set(ids)) != len(ids)
                or any(type(v) is not int or v <= 0 for v in ids)
                or before['score'] + count * 30000 < before['final_threshold']):
            raise ValueError('最终积分消费意图缺少完整目标和任务范围')
    path = _path(activity_id)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if any(row['status'] == 'armed' for row in rows):
            raise RuntimeError('丹道积分存在未结消费意图，禁止再次炼制')
        action_id = str(uuid4())
        rows.append(dict(action_id=action_id, status='armed', before=before, count=count))
        _write(path, rows)
        return action_id


def settle_dandao_score_batch(activity_id: int, action_id: str, after: dict) -> dict:
    """Verify exact score, or an explicitly armed final milestone proof.

    After the final milestone the server may cap every task progress. That
    terminal proves a lower bound, never an invented exact accumulated score.
    Material loss and process ownership remain exact in both modes.
    """
    after = {**after, 'materials': {str(k): v for k, v in after.get('materials', {}).items()}}
    path = _path(activity_id)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = json.loads(path.read_text(encoding='utf-8'))
        matches = [row for row in rows if row['action_id'] == action_id]
        if len(matches) != 1:
            raise RuntimeError('丹道积分消费意图身份不唯一')
        row = matches[0]
        before, count = row['before'], row['count']
        before_materials, after_materials = before['materials'], after['materials']
        exact_score = after.get('score') == before['score'] + count * 30000
        proof = after.get('final_threshold_proof') or {}
        final_proof = (before.get('final_threshold') is not None and after.get('score') is None
                       and proof.get('complete') is True
                       and proof.get('threshold') == before['final_threshold']
                       and sorted(proof.get('task_ids') or []) == sorted(before['task_ids'])
                       and sorted(proof.get('satisfied_task_ids') or []) == sorted(before['task_ids']))
        if (row['status'] != 'armed' or after.get('evidence') != before['evidence']
                or not (exact_score or final_proof)
                or set(after_materials) != set(before_materials)
                or any(after_materials[k] < 0 or after_materials[k] > before_materials[k]
                       for k in before_materials)
                or sum(before_materials.values()) - sum(after_materials.values()) != count * 12):
            raise RuntimeError('丹道积分或材料变化未形成准确消费终态，保留意图禁止重放')
        row.update(status='verified', after=after,
                   score_verification='exact' if exact_score else 'final_threshold_lower_bound')
        _write(path, rows)
        return row
