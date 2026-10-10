"""Project committed ranking tails into the existing engineering notification lane.

Disabled unless an owner explicitly configures an existing fixed subscription.
The configured thread is a routing binding, never a claim that Codex produced
the business result. This provider neither sends nor consumes group questions.
Recovery calls the same public exact-attempt API; it never replays the game.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from filelock import FileLock

from backend.core.fanxiu.data_annotation.state import read_data_annotation_json, write_data_annotation_json
from backend.core.fanxiu.data_annotation.kernel_scheduler_results import read_scheduler_cell_result


def _configuration_path(path):
    if path is not None:
        return Path(path)
    from backend.core.fanxiu.behavior_tree.kernel_scheduler import fanxiu_kernel_scheduler_state_path
    return fanxiu_kernel_scheduler_state_path().parent / 'ranking-completion-reporting.json'


def _store(store):
    if store is not None:
        return store
    from backend.core.messaging.codex_results import result_store
    return result_store()


def _validate_binding(config, store):
    """Only an existing subscription may bind the fixed engineering recipient."""
    hooks = store.status()['hooks']
    if not any(h['key'] == config['hook_key'] and h['account_id'] == config['account_id']
               and h['chat_id'] == config['chat_id'] for h in hooks):
        raise ValueError('榜单汇报目的群与工程 hook 不一致')
    if not any(s['thread_id'] == config['thread_id'] and s['hook_key'] == config['hook_key']
               and s['enabled'] for s in store.codex_notifications_status()['subscriptions']):
        raise ValueError('榜单汇报须绑定本群已启用的订阅')


def configure_ranking_completion_reporting(*, hook_key, account_id, chat_id, thread_id,
                                          enabled=False, configuration_path=None, store=None):
    """Owner-authorized configuration only; default disabled and no publication.

Use a domain reporting subscription chosen by the owner, not another agent's
identity inferred from the game receipt. No subscription is created here.
"""
    config = dict(hook_key=hook_key, account_id=account_id, chat_id=chat_id,
                  thread_id=thread_id, enabled=bool(enabled))
    if not all(isinstance(config[k], str) and config[k].strip() for k in
               ('hook_key', 'account_id', 'chat_id', 'thread_id')):
        raise ValueError('固定汇报绑定必须完整')
    _validate_binding(config, _store(store))
    path = _configuration_path(configuration_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock', timeout=15):
        prior = read_data_annotation_json(path, None)
        if prior and any(prior.get(k) != config[k] for k in
                         ('hook_key', 'account_id', 'chat_id', 'thread_id')):
            raise ValueError('已有汇报绑定不可改换来源或目的群')
        write_data_annotation_json(path, config)
    return config


def project_ranking_tail_result(item):
    """Pure receipt projection; no configuration, store, message or game access."""
    checkpoint, result = item['checkpoint'], item['result']
    if checkpoint.get('checkpoint_kind') != 'exchange_tail_0030' or result.get('status') != 'completed':
        return None
    if checkpoint.get('activity_type') != 'magic-invasion':
        return None  # Other domains need their own accepted summary contract.
    identity = {k:checkpoint[k] for k in ('instance_key', 'checkpoint_kind', 'business_date')}
    if not all(isinstance(v, str) and v for v in identity.values()):
        raise ValueError('尾单 checkpoint 身份不完整')
    purchases, planning = result['purchases'], result['planning']
    spend = sum(p['quantity'] * p['unit_price'] for p in purchases)
    remaining = result['currency_remaining']
    if (not purchases or any(type(p.get('quantity')) is not int or p['quantity'] <= 0
                            or type(p.get('unit_price')) is not int or p['unit_price'] < 0 for p in purchases)
            or type(remaining) is not int or remaining < 0
            or spend != planning['planned_spend_tokens']
            or remaining != planning['planned_remaining_tokens']
            or type(planning.get('closing_goods_reached')) is not bool):
        raise ValueError('尾单实际兑换与预算凭证不一致')
    from backend.core.messaging.codex_results import format_fanxiu_amount
    closing = '最终商品目标已达' if planning['closing_goods_reached'] else '原最终商品预算目标未达'
    remaining_text = str(remaining) if remaining < 10000 else format_fanxiu_amount(remaining)
    text = (f'【凡修工程进度】\n魔道兑换尾单：兑换{len(purchases)}种，'
            f'花费{format_fanxiu_amount(spend)}魔晶，余额{remaining_text}。'
            f'{closing}；作业成功仅表示本次预算下的兑换收尾完成。')
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return digest, identity, text


def publish_committed_ranking_attempt(task_id, attempt_id, *, configuration_path=None,
                                     directory=None, store=None):
    """Queue only accepted exact committed tails, once per business checkpoint.

The original attempt and payload are frozen before atomic notification admission.
After an enqueue/marker crash, calling this exact API recovers the same identity.
Sending, sent and uncertain notifications never cause a second admission.
"""
    config = read_data_annotation_json(_configuration_path(configuration_path), {}) or {}
    if not config.get('enabled'):
        return {'status': 'disabled'}  # Do not even construct the production store.
    try:
        record_ranking_reporting_obligation(task_id, attempt_id, status='pending',
            configuration_path=configuration_path, directory=directory)
        result = _publish_enabled_attempt(task_id, attempt_id, config=config,
            directory=directory, store=store)
        record_ranking_reporting_obligation(task_id, attempt_id, status=result['status'],
            configuration_path=configuration_path, directory=directory)
        return result
    except Exception as exc:
        record_ranking_reporting_obligation(task_id, attempt_id, status='error', error=str(exc),
            configuration_path=configuration_path, directory=directory)
        raise


def record_ranking_reporting_obligation(task_id, attempt_id, *, status, error='',
                                       configuration_path=None, directory=None):
    """Persist exact committed handoff diagnostics, without admission or sending.

Recovery uses publish_committed_ranking_attempt with this same original attempt.
Disabled configuration leaves the obligation intact. CAS losers have no exact
committed receipt and cannot create an obligation. This journal is independent
of the messaging store, so a queue/database outage remains diagnosable.
"""
    receipt = read_scheduler_cell_result(task_id, attempt_id, directory=directory)
    if task_id != 'ranking-lifecycle' or not receipt.get('found') or receipt.get('scheduler_result') != 'success':
        return {'status': 'pending_evidence'}
    value = dict(task_id=task_id, attempt_id=attempt_id, status=status, error=error,
                 origin='ranking_terminal_receipt')
    path = _configuration_path(configuration_path).parent / 'ranking-report-obligations' / (attempt_id+'.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path)+'.lock', timeout=15):
        write_data_annotation_json(path, value)
    return value


def read_ranking_reporting_obligation(task_id, attempt_id, *, configuration_path=None):
    """Read one exact durable handoff; absent evidence never creates directories."""
    import re
    if not isinstance(attempt_id, str) or not re.fullmatch('[a-f0-9]{32}', attempt_id):
        raise ValueError('汇报义务需要精确 attempt_id')
    path = _configuration_path(configuration_path).parent / 'ranking-report-obligations' / (attempt_id+'.json')
    value = read_data_annotation_json(path, None)
    if value and value['task_id'] != task_id:
        raise ValueError('汇报义务 task 身份不一致')
    return value or {'status':'not_recorded', 'task_id':task_id, 'attempt_id':attempt_id}


def _publish_enabled_attempt(task_id, attempt_id, *, config, directory, store):
    if task_id != 'ranking-lifecycle':
        return {'status': 'not_applicable'}
    receipt = read_scheduler_cell_result(task_id, attempt_id, directory=directory)
    if not receipt.get('found') or receipt.get('scheduler_result') != 'success':
        return {'status': 'pending_evidence', 'reason': 'committed_success_receipt_required'}
    projections = [p for item in receipt['business_result'].get('checkpoint_results', [])
                   if (p := project_ranking_tail_result(item)) is not None]
    if not projections:
        return {'status': 'not_applicable'}
    store = _store(store)
    _validate_binding(config, store)
    reports = []
    with FileLock(str(store.path) + '.ranking-report.lock', timeout=15):
        for digest, identity, text in projections:
            marker = 'ranking_completion_report:' + digest
            prior = store.get_meta(marker)
            if prior is None:
                prior = dict(identity=identity, task_id=task_id, attempt_id=attempt_id,
                             thread_id=config['thread_id'], hook_key=config['hook_key'],
                             turn_id='stage:ranking-checkpoint:' + digest,
                             text=text + f'\n正式attempt：{attempt_id}', origin='ranking_terminal_receipt')
                store.set_meta(marker, prior)
            if prior['thread_id'] != config['thread_id'] or prior['hook_key'] != config['hook_key']:
                raise ValueError('已有 checkpoint 汇报绑定不可换源重发')
            notification = store.codex_notification(prior['thread_id'], prior['turn_id'])
            if notification is None:
                store.enqueue_codex_completion(prior['thread_id'], prior['turn_id'], prior['text'])
                notification = store.codex_notification(prior['thread_id'], prior['turn_id'])
            if notification is None:
                reports.append({**prior, 'status': 'pending_admission'})
                continue
            if notification['text'] != prior['text'] or notification['hook_key'] != prior['hook_key']:
                raise ValueError('已有通知身份与原始汇报凭证冲突')
            reports.append({**prior, 'notification_id': notification['id'], 'status': notification['status']})
    return {'status': 'pending_admission' if any(r['status'] == 'pending_admission' for r in reports) else 'recorded',
            'reports': reports}


def read_ranking_completion_report(*, instance_key, checkpoint_kind, business_date, store=None):
    """Read exact notification and engineering archive proof, never send or poll game."""
    identity = dict(instance_key=instance_key, checkpoint_kind=checkpoint_kind, business_date=business_date)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    store = _store(store)
    record = store.get_meta('ranking_completion_report:' + digest)
    if record is None:
        return {'status': 'not_recorded', 'identity': identity}
    notification = store.codex_notification(record['thread_id'], record['turn_id'])
    if notification is None:
        return {**record, 'status': 'pending_admission'}
    verification = store.get_meta(f"codex_result_verification:{notification['id']}", {})
    return {**record, 'notification_id': notification['id'], 'delivery_status': notification['status'],
            'status': 'verified' if verification.get('status') == 'verified' else notification['status'],
            'archive_verification': verification}
