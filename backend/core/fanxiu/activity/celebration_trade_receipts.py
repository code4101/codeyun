"""Atomic start intents prevent replaying unresolved native trade batches."""
import json
import os
from uuid import uuid4
from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path(runtime_id):
    if type(runtime_id) is not int or runtime_id <= 0:
        raise ValueError('Expected positive runtime ID')
    folder = fanxiu_data_annotation_dir() / 'celebration-trade-receipts'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{runtime_id}.json'


def read_celebration_trade_receipts(runtime_id: int) -> list[dict]:
    path = _path(runtime_id)
    with FileLock(str(path) + '.lock', timeout=5):
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def arm_celebration_trade_batch(runtime_id: int, before: dict, settings: dict) -> str:
    """Persist before the sole Start click; any unresolved intent blocks replay."""
    count = settings.get('selected_trade_count')
    if (before.get('complete') is not True or settings.get('complete') is not True
            or settings.get('default_plan_selected') is not True
            or type(count) is not int or count <= 0
            or count > before['license_count'] or before['money_count'] <= 0
            or before['finish_window_open']):
        raise ValueError('商盟批次开始事实未完整或不满足原生默认方案资源门禁')
    path = _path(runtime_id)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if any(r['status'] == 'armed' for r in rows):
            raise RuntimeError('商盟存在未结自动批次，禁止再次开始')
        action_id = str(uuid4())
        rows.append(dict(action_id=action_id, status='armed', before=before, settings=settings))
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
        return action_id


def settle_celebration_trade_batch(runtime_id: int, action_id: str, after: dict) -> dict:
    """Accept only exact server sale progress plus a naturally loaded finish UI."""
    path = _path(runtime_id)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = json.loads(path.read_text(encoding='utf-8'))
        matches = [r for r in rows if r['action_id'] == action_id]
        if len(matches) != 1:
            raise RuntimeError('商盟批次回执身份不唯一')
        row = matches[0]
        before, settings = row['before'], row['settings']
        count = settings['selected_trade_count']
        if (after.get('complete') is not True or not after['finish_window_open'] or after['completed_count'] != count
                or after['requested_count'] != count
                or after['selected_plan_uid'] != settings['selected_plan_uid']
                or after['total_sell_count'] != before['total_sell_count'] + count
                or after['evidence'] != before['evidence']):
            raise RuntimeError('商盟自动批次未形成准确原生终态，保留意图禁止重放')
        row.update(status='verified', after=after)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
        return row
