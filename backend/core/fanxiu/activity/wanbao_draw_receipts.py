"""Persistent single-draw receipts for the 万宝臻宝 Mining lottery.

One sealed intent per 启宝 click.  An unresolved intent blocks every new
draw; reentry only settles the original intent from a fresh Runtime snapshot
and never clicks again.  A retainable-ticket grand prize already present in
Runtime or in a verified receipt refuses any further intent.
"""
import json
import os
from datetime import datetime
from typing import Any
from uuid import uuid4

from filelock import FileLock

from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


WANBAO_DRAW_CURRENCY = 40017


def _path():
    return fanxiu_data_annotation_dir() / 'wanbao-draw-actions.json'


def _read(path):
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('万宝抽奖回执不可读')
    return rows


def _write(path, rows):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def wanbao_draw_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Minimal coherent projection that a receipt compares across one click."""
    if snapshot.get('complete') is not True:
        raise ValueError(str(snapshot.get('reason') or '万宝臻宝 Runtime 不完整'))
    draw = snapshot.get('draw') or {}
    evidence = snapshot.get('evidence') or {}
    activity_id = snapshot.get('activity_id')
    if (type(activity_id) is not int or activity_id <= 0
            or draw.get('cost_type') != WANBAO_DRAW_CURRENCY or draw.get('cost_per_draw') != 1):
        raise ValueError('万宝抽奖不在已核实 40017 单抽白名单')
    currency = draw.get('available_currency')
    progress = draw.get('progress')
    if type(currency) is not int or currency < 0 or type(progress) is not int or progress < 0:
        raise ValueError('万宝抽奖余额或累计进度不可读')
    pool = draw.get('pool_items') or []
    if not pool or any('40017' in str(row.get('reward') or '') for row in pool):
        raise ValueError('万宝奖池为空或返还消费币，精确扣币凭证不适用')
    return {
        'activity_id': activity_id,
        'currency': currency,
        'progress': progress,
        'grand_prize_id': int((draw.get('grand_prize') or {}).get('id') or 0),
        'grand_prize_hits': int(draw.get('y') or 0),
        'hit_counts': {str(row['id']): int(row['hit_count']) for row in pool},
        'pid': evidence.get('pid'),
        'process_start_ticks': evidence.get('process_start_ticks'),
        'captured_at': snapshot.get('captured_at'),
    }


def verify_wanbao_single_draw(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Prove exactly one debit, one progress step and one pool hit."""
    if (before['activity_id'] != after['activity_id']
            or before['pid'] != after['pid']
            or before['process_start_ticks'] != after['process_start_ticks']):
        raise ValueError('万宝抽奖前后活动或进程改变，保留未结回执')
    if (before['currency'] - after['currency'] != 1
            or after['progress'] - before['progress'] != 1):
        raise ValueError('原单抽消费/进度尚未精确结算')
    deltas = {key: after['hit_counts'].get(key, 0) - value
              for key, value in before['hit_counts'].items()}
    hit = [key for key, delta in deltas.items() if delta == 1]
    if len(hit) != 1 or any(delta not in (0, 1) for delta in deltas.values()):
        raise ValueError(f'原单抽命中记录尚未精确结算：{deltas}')
    reward_id = int(hit[0])
    grand = int(reward_id == before['grand_prize_id'])
    if after['grand_prize_hits'] - before['grand_prize_hits'] != grand:
        raise ValueError('大奖命中计数与奖池命中记录不一致')
    return {'draw_count': 1, 'cost': 1, 'reward_id': reward_id, 'grand_prize_count': grand,
            'currency_before': before['currency'], 'currency_after': after['currency'],
            'progress_after': after['progress']}


def read_wanbao_draw_actions():
    path = _path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


def begin_wanbao_single_draw(before: dict[str, Any]) -> dict[str, Any]:
    """Seal one intent before clicking; a verified grand prize stops the ticket."""
    if before['currency'] < 1:
        raise ValueError('万宝抽奖余额不足一抽')
    if before['grand_prize_hits'] > 0:
        raise RuntimeError('万宝当期首个大奖已在 Runtime 中，保留剩余 40017')
    path = _path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        if any(r.get('status') == 'pending' for r in rows):
            raise RuntimeError('原万宝抽奖尚未结算，禁止重复提交')
        if any(r.get('activity_id') == before['activity_id'] and r.get('status') == 'verified'
               and r.get('effect', {}).get('grand_prize_count', 0) > 0 for r in rows):
            raise RuntimeError('万宝当期首个大奖已核实，保留剩余 40017')
        known_draws = sum(r.get('effect', {}).get('draw_count', 0) for r in rows
                          if r.get('activity_id') == before['activity_id'] and r.get('status') == 'verified')
        if before['progress'] != known_draws:
            raise RuntimeError(
                f"万宝当期累计 {before['progress']} 抽与已核实凭证 {known_draws} 抽不一致，禁止新抽奖")
        row = {'action_id': str(uuid4()), 'action_kind': 'single_draw',
               'activity_id': before['activity_id'], 'status': 'pending', 'before': before,
               'created_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        rows.append(row); _write(path, rows)
        return row


def settle_wanbao_single_draw(action_id: str, after: dict[str, Any]) -> dict[str, Any]:
    """Idempotently settle the original intent; failed checks never draw again."""
    path = _path()
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        matches = [r for r in rows if r.get('action_id') == action_id]
        if len(matches) != 1:
            raise ValueError('万宝原始intent不存在或不唯一')
        row = matches[0]
        if row.get('status') == 'verified':
            return row
        if row.get('status') != 'pending':
            raise ValueError('万宝intent状态不允许结算')
        effect = verify_wanbao_single_draw(row['before'], after)
        row.update(status='verified', effect=effect, after=after,
                   verified_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        _write(path, rows)
        return row


def void_wanbao_single_draw(action_id: str, after: dict[str, Any], *,
                            min_age_seconds: float = 60.0) -> dict[str, Any]:
    """Close a pending intent whose click provably had no effect.

    Only an unchanged balance, progress, every hit count and process identity,
    observed at least ``min_age_seconds`` after sealing, qualifies.  A late
    draw after voiding is still caught by the progress/receipt guard in
    :func:`begin_wanbao_single_draw`.
    """
    path = _path()
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        matches = [r for r in rows if r.get('action_id') == action_id]
        if len(matches) != 1 or matches[0].get('status') != 'pending':
            raise ValueError('万宝待作废intent不存在或不是 pending')
        row = matches[0]
        before = row['before']
        age = datetime.now().astimezone().timestamp() - datetime.fromisoformat(row['created_at']).timestamp()
        keys = ('activity_id', 'currency', 'progress', 'grand_prize_hits', 'hit_counts',
                'pid', 'process_start_ticks')
        if age < min_age_seconds or any(before[k] != after[k] for k in keys):
            raise ValueError('万宝intent尚不能证明无效果')
        row.update(status='no_effect', after=after,
                   voided_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        _write(path, rows)
        return row


def _store_policy_path():
    return fanxiu_data_annotation_dir() / 'wanbao-store-policy.json'


def record_wanbao_store_policy_skip(activity_id: int, *, reason: str) -> dict[str, Any]:
    """Durably record, once per activity, that the store was skipped unpurchased."""
    if type(activity_id) is not int or activity_id <= 0:
        raise ValueError('万宝商店跳过记录需要精确活动 ID')
    path = _store_policy_path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        for row in rows:
            if row.get('activity_id') == activity_id and row.get('outcome') == 'policy_skip':
                return row
        row = {'activity_id': activity_id, 'outcome': 'policy_skip', 'purchased': False,
               'reason': reason,
               'recorded_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        rows.append(row); _write(path, rows)
        return row


def read_wanbao_store_policy_records():
    path = _store_policy_path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


__all__ = [
    'WANBAO_DRAW_CURRENCY',
    'begin_wanbao_single_draw',
    'read_wanbao_store_policy_records',
    'record_wanbao_store_policy_skip',
    'read_wanbao_draw_actions',
    'settle_wanbao_single_draw',
    'verify_wanbao_single_draw',
    'void_wanbao_single_draw',
    'wanbao_draw_projection',
]
