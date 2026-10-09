"""Occurrence-scoped training receipts; unresolved actions never authorize replay.

The provider owns paths, locking and atomic publication. A receipt proves a
settled node/material effect, separately from aggregate clan score calibration.
Only real Runtime observations supplied by the sole operator belong here.
"""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path(occurrence_key, receipt_dir):
    parts = str(occurrence_key).split('|')
    if len(parts) != 4 or any(not part.isdecimal() for part in parts):
        raise ValueError('炼体凭证要求完整活动实例键')
    activity, start, end, cross = map(int, parts)
    if activity != 8043401 or end <= start or start <= 0 or cross != 8:
        raise ValueError('炼体凭证活动身份不符')
    folder = Path(receipt_dir) if receipt_dir is not None else fanxiu_data_annotation_dir() / 'shequn-lianti-receipts'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (hashlib.sha256(occurrence_key.encode()).hexdigest() + '.json')


def _read(path):
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('炼体消费凭证损坏')
    return rows


def _save(path, rows):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_shequn_lianti_training_receipts(occurrence_key, *, receipt_dir=None):
    """Read this exact period only; unreadable data fails closed."""
    path = _path(occurrence_key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


def begin_shequn_lianti_training_action(*, goal, dialog, receipt_dir=None):
    """Reserve one verified configuration immediately before its sole click.

    A pending reservation is deliberately conservative even if the click later
    fails: only effect reconciliation can clear it, never a retry timeout.
    """
    if (not goal.get('complete') or not goal.get('progress_is_exact')
            or not goal.get('training_binding_verified') or not dialog.get('complete')
            or not dialog.get('selection_cost_verified')
            or dialog.get('activity_id') != goal.get('training_activity_id')
            or dialog.get('auto_buy_enabled') is not False
            or dialog.get('multiplicity_enabled') is not False
            or dialog.get('breakthrough_material_cost') != 0):
        raise ValueError('炼体消费缺少当前实例及精确配置门禁')
    fields = ('base_id', 'current_node', 'stage', 'selected_nodes', 'normal_item_id',
              'normal_material_cost', 'available_normal_material')
    if any(type(dialog.get(k)) is not int or dialog[k] < 0 for k in fields):
        raise ValueError('炼体消费数值不完整')
    if (dialog['base_id'] <= 0 or dialog['normal_item_id'] <= 0
            or not 1 <= dialog['selected_nodes'] <= 512
            or not 0 < dialog['normal_material_cost'] <= dialog['available_normal_material']
            or goal['activity_progress'] >= goal['target_score']):
        raise ValueError('炼体消费超过库存、单批范围或任务目标')
    key = goal['occurrence_key']
    path = _path(key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        if any(row.get('status') == 'pending' for row in rows):
            raise RuntimeError('炼体已有待核验效果，必须按原action_id回读')
        action_id = uuid4().hex
        rows.append(dict(action_id=action_id, status='pending', occurrence_key=key,
                         goal_before=goal, dialog_before=dialog,
                         submitted_at=datetime.now().astimezone().isoformat()))
        _save(path, rows)
        return action_id


def reconcile_shequn_lianti_training_action(*, occurrence_key, action_id,
                                           book, inventory_count, goal, receipt_dir=None):
    """Verify exact settled effects; mismatches remain pending with evidence.

    Unchanged state is ambiguous (server latency or an unsubmitted click), so
    it never grants replay. Clan progress is recorded but cannot establish an
    individual scoring rate by itself. Repeated reads of a verified ID return
    its original receipt without editing it.
    """
    path = _path(occurrence_key, receipt_dir)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        row = next((r for r in rows if r.get('action_id') == action_id), None)
        if row is None:
            raise ValueError('未知炼体action_id')
        if row['status'] == 'verified':
            return row
        before = row['dialog_before']
        matched = (goal.get('complete') and goal.get('occurrence_key') == occurrence_key
                   and book.get('base_id') == before['base_id']
                   and book.get('stage') == before['stage']
                   and book.get('current_node') == before['current_node'] + before['selected_nodes']
                   and type(inventory_count) is int
                   and inventory_count == before['available_normal_material'] - before['normal_material_cost'])
        row['last_observation'] = dict(book=book, inventory_count=inventory_count, goal=goal)
        if matched:
            row.update(status='verified', verified_at=datetime.now().astimezone().isoformat())
        _save(path, rows)
        return row
