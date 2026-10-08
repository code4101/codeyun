"""Durable gift actions: uncertain submissions are reconciled, never replayed."""
import json
import os
from datetime import datetime
from uuid import uuid4

from filelock import FileLock

from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path():
    return fanxiu_data_annotation_dir() / 'xianyuan-gift-actions.json'


def _read(path):
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('仙缘赠礼回执不可读')
    return rows


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_xianyuan_gift_actions():
    """Read original action identities and verified effects through the provider."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


def recovered_npc_reward_receipt(actions, before, after):
    """Associate fresh reward recovery with the latest verified NPC gift.

    A client restart may change PID. The original consumption remains sealed;
    the two recovery observations must share their own process lifetime. Never
    attach a newer reward cycle to an older gift solely by matching reward IDs.
    """
    if not before.get('runtime_complete') or not after.get('runtime_complete'):
        raise ValueError('回礼恢复需要完整原生事实')
    prior, current = before.get('people', []), after.get('people', [])
    if len(prior) != 1 or len(current) != 1 or prior[0]['npc_id'] != current[0]['npc_id']:
        raise ValueError('回礼恢复人物身份不唯一')
    npc_id = prior[0]['npc_id']
    rows = [row for row in actions if row.get('batch', {}).get('npc_id') == npc_id]
    if not rows:
        return None
    latest = rows[-1]
    if latest.get('status') != 'verified':
        raise ValueError('回礼恢复不能代替未结消费核验')
    originals = [person for person in latest['after'].get('people', []) if person['npc_id'] == npc_id]
    if len(originals) != 1:
        raise ValueError('原始赠礼人物凭证不唯一')
    original = originals[0]
    track = latest['batch']['progress_track']
    process = before['runtime_debug']
    if (any(not process.get(key) or process.get(key) != after['runtime_debug'].get(key)
            for key in ('pid', 'process_start_ticks'))
            or original['npc_id'] != npc_id
            or not before.get('runtime_updated_at')
            or after.get('runtime_updated_at', 0) < before['runtime_updated_at']
            or before['runtime_updated_at'] < latest['after'].get('runtime_updated_at', 0)
            or original.get(track) != prior[0].get(track)
            or prior[0].get(track) != current[0].get(track)):
        raise ValueError('回礼恢复进程或原始赠礼周期不符')
    eligible = {r['reward_key'] for r in original['rewards'] if r['state'] == 1}
    prior_states = {r['reward_key']: r['state'] for r in prior[0]['rewards']}
    claimed = {r['reward_key'] for r in current[0]['rewards'] if r['state'] == 2}
    if (not eligible or any(prior_states.get(key) not in (1, 2) for key in eligible)
            or not eligible <= claimed or any(r['state'] == 1 for r in current[0]['rewards'])):
        raise ValueError('原交易回礼尚未完整领取')
    return {'action_id': latest['action_id'], 'npc_id': npc_id,
            'eligible_reward_keys': sorted(eligible), 'claimed_reward_keys': sorted(claimed),
            'claimable_after': [r['reward_key'] for r in current[0]['rewards'] if r['state'] == 1],
            'evidence': after['runtime_debug'], 'recovered_existing_gift': True,
            'original_gift_process': {key: latest['after']['runtime_debug'][key]
                                      for key in ('pid', 'process_start_ticks')}}


def begin_xianyuan_gift_action(batch, before, *, inventory_before, inventory_before_evidence):
    """Record one bounded submission before clicking the real gift button.

    Any unresolved action blocks a new submission, including one from another
    day or NPC. The caller must retain this action ID across observation timeouts.
    """
    if batch.get('action') != 'gift' or type(batch.get('quantity')) is not int or batch['quantity'] <= 0:
        raise ValueError('需要明确有界赠礼批次')
    process = before.get('runtime_debug', {})
    people = [p for p in before.get('people', []) if p.get('npc_id') == batch.get('npc_id')
              and p.get('runtime_index') == batch.get('runtime_index')]
    if not before.get('runtime_complete') or len(people) != 1 or any(
        not process.get(k) for k in ['pid', 'process_start_ticks']
    ) or people[0].get(batch.get('progress_track')) != batch.get('favor_before'):
        raise ValueError('赠礼前原生身份或进度不完整')
    if type(inventory_before) is not int or inventory_before < batch['quantity']:
        raise ValueError('赠礼库存不足或不可读')
    if any(inventory_before_evidence.get(k) != process.get(k) for k in ['pid', 'process_start_ticks']) or (
        batch['base_id'] not in inventory_before_evidence.get('requested_item_ids', [])
        or inventory_before_evidence.get('source') != 'BackpackMgr.Model.BackpackData.ItemVoDic'
        or inventory_before_evidence.get('read_only') is not True
    ):
        raise ValueError('赠礼库存来源与人物进程或目标物品不符')
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        if any(row.get('status') == 'pending' for row in rows):
            raise RuntimeError('原赠礼批次尚未结算，禁止重复提交')
        row = {'action_id': str(uuid4()), 'status': 'pending', 'batch': dict(batch),
               'before': before, 'inventory_before': inventory_before,
               'inventory_before_evidence': inventory_before_evidence,
               'created_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        rows.append(row)
        _write(path, rows)
        return row


def settle_xianyuan_gift_action(action_id, after, *, inventory_after, inventory_after_evidence):
    """Verify and settle the original effect; repeated reads cannot consume again."""
    from backend.core.fanxiu.data_annotation.tasks.xianyuan_auto_gift import verify_xianyuan_gift_effect
    path = _path()
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        matches = [row for row in rows if row.get('action_id') == action_id]
        if len(matches) != 1:
            raise ValueError('赠礼原始回执不存在或不唯一')
        row = matches[0]
        if row.get('status') == 'verified':
            return row
        if row.get('status') != 'pending':
            raise ValueError('赠礼回执状态不支持结算')
        effect = verify_xianyuan_gift_effect(row['batch'], row['before'], after,
            inventory_before=row['inventory_before'], inventory_after=inventory_after,
            inventory_before_evidence=row['inventory_before_evidence'], inventory_after_evidence=inventory_after_evidence)
        row.update(status='verified', effect=effect, after=after,
                   inventory_after=inventory_after,
                   inventory_after_evidence=inventory_after_evidence,
                   verified_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        _write(path, rows)
        return row
