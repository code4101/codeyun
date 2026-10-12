"""Persistent single-draw receipts for a loaded prize wheel.

An unresolved submission blocks every new draw. Reopening or a process change
cannot manufacture a receipt from the wheel's persistent last animation target.
Single draws give an exact hit count, unlike a ten-draw animation's any-hit flag.
"""
import json
import os
from datetime import datetime
from uuid import uuid4

from filelock import FileLock
from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_data_annotation_dir


def _path():
    return fanxiu_data_annotation_dir() / 'prize-wheel-actions.json'


def _read(path):
    rows = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError('宝盘回执不可读')
    return rows


def _write(path, rows):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def prize_wheel_instance(snapshot):
    """Exact server period identity; activity names cannot identify an occurrence."""
    view = snapshot['view']
    values = [view.get(k) for k in ('activity_id', 'runtime_id', 'start_time_ms', 'end_time_ms')]
    if any(type(v) is not int or v <= 0 for v in values) or values[2] > values[3]:
        raise ValueError('宝盘活动周期不完整')
    return ':'.join(map(str, values))


def validate_prize_wheel_observation(snapshot):
    """Require one active page, exact currency and coherent process observations."""
    view, wheel, wallet = (snapshot[k] for k in ('view', 'wheel', 'wallet'))
    if view.get('complete') is not True or wheel.get('complete') is not True:
        raise ValueError('宝盘事实不完整')
    if any(type(wheel.get(k)) is not int or wheel[k] < 0 for k in ('progress', 'gift_balance')):
        raise ValueError('宝盘累计次数或赠礼余额不可读')
    prize_wheel_instance(snapshot)
    process = view.get('evidence', {})
    if any(type(process.get(k)) is not int or process[k] <= 0
           or any(other.get('evidence', {}).get(k) != process[k] for other in (wheel, wallet))
           for k in ('pid', 'process_start_ticks')):
        raise ValueError('宝盘观测进程不一致')
    if (view.get('activity_id') != wheel.get('activity_id') or not view.get('panel_address')
            or type(view.get('ten_draw')) is not bool):
        raise ValueError('宝盘页面身份不完整')
    config = wheel.get('configuration', {})
    if (config.get('available') is not True or config.get('currency_type') != 40017
            or config.get('cost_per_draw') != 1 or config.get('gift_item_id') != 40003
            or view.get('cost_item_id') != 40017 or wallet.get('currency_type') != 40017):
        raise ValueError('当期消费配置不在已核实仙符白名单')
    amount = wallet.get('currency_amount')
    if (type(amount) is not int or amount < 0 or view.get('displayed_currency_amount') != amount
            or wallet.get('currency_borrow') != 0):
        raise ValueError('宝盘消费余额未同步或存在借币')
    native = wheel.get('wheel_rewards', [])
    if ([{'id': r['id'], 'is_big_reward': r['is_big_reward']} for r in native]
            != view.get('wheel_rewards') or not native):
        raise ValueError('宝盘页面与原生奖池不一致')
    # This receipt proves a debit, so rewards returning the cost currency need
    # a different projection. Refuse that unsupported pool rather than infer it.
    if any('40017' in r.get('reward', '') for r in native):
        raise ValueError('奖池返还消费币，当前精确扣币凭证不适用')


def verify_prize_wheel_single_draw(before, after):
    """Prove one fresh debit and its settled response in the original page.

Flower/progress updates can arrive separately and are not used as a receipt.
The exclusive game actor submits exactly one single draw, and this pool cannot
refund its currency. A debit plus settled target belongs to that submission.
"""
    for snapshot in (before, after):
        validate_prize_wheel_observation(snapshot)
    if prize_wheel_instance(before) != prize_wheel_instance(after):
        raise ValueError('宝盘消费跨活动周期')
    b, a = before['view'], after['view']
    if (any(b['evidence'][k] != a['evidence'][k] for k in ('pid', 'process_start_ticks'))
            or b['panel_address'] != a['panel_address']):
        raise ValueError('宝盘消费后页面或进程改变，保留未结回执')
    if b['ten_draw'] or a['ten_draw']:
        raise ValueError('单抽凭证不能接受十连模式')
    if before['wallet']['currency_amount'] - after['wallet']['currency_amount'] != 1:
        raise ValueError('原单抽消费尚未精确结算')
    target = a.get('last_response_target')
    if (a.get('waiting_response') is not False or a.get('animation_running') is not False
            or target not in a['wheel_rewards']):
        raise ValueError('原单抽结果尚未稳定')
    return {'draw_count': 1, 'cost': 1, 'reward_id': target['id'],
            'grand_prize_count': int(target['is_big_reward']),
            'currency_before': before['wallet']['currency_amount'],
            'currency_after': after['wallet']['currency_amount']}


def read_prize_wheel_actions():
    """Read original intents and verified results without touching the game."""
    path = _path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        return _read(path)


def begin_prize_wheel_single_draw(before, *, retainability):
    """Seal one intent before clicking; prior hits stop retainable-ticket draws."""
    validate_prize_wheel_observation(before)
    if retainability not in ('retainable', 'non_retainable'):
        raise ValueError('需要已核实跨期留存性')
    if (before['view']['ten_draw'] or before['wallet']['currency_amount'] < 1
            or before['view'].get('waiting_response') is True
            or before['view'].get('animation_running') is True):
        raise ValueError('宝盘不在可单抽稳定边界')
    key = prize_wheel_instance(before)
    path = _path(); path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        if any(r.get('status') == 'pending' for r in rows):
            raise RuntimeError('原宝盘抽奖尚未结算，禁止重复提交')
        known_draws = sum(r.get('effect', {}).get('draw_count', 0) for r in rows
                          if r.get('instance_key') == key and r.get('status') == 'verified')
        if before['wheel'].get('progress', 0) > known_draws:
            raise RuntimeError('当期存在未纳入凭证的历史抽奖，首奖状态需先核实')
        if retainability == 'retainable' and any(
                r.get('instance_key') == key and r.get('status') == 'verified'
                and r.get('effect', {}).get('grand_prize_count', 0) > 0 for r in rows):
            raise RuntimeError('当期首个大奖已核实，保留剩余仙符')
        row = {'action_id': str(uuid4()), 'instance_key': key, 'status': 'pending',
               'retainability': retainability, 'before': before,
               'created_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        rows.append(row); _write(path, rows)
        return row


def settle_prize_wheel_single_draw(action_id, after):
    """Idempotently settle the original action; failed checks never consume again."""
    path = _path()
    with FileLock(str(path) + '.lock', timeout=5):
        rows = _read(path)
        matches = [r for r in rows if r.get('action_id') == action_id]
        if len(matches) != 1:
            raise ValueError('宝盘原始intent不存在或不唯一')
        row = matches[0]
        if row.get('status') == 'verified':
            return row
        if row.get('status') != 'pending':
            raise ValueError('宝盘intent状态不允许结算')
        effect = verify_prize_wheel_single_draw(row['before'], after)
        row.update(status='verified', effect=effect, after=after,
                   verified_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        _write(path, rows)
        return row
