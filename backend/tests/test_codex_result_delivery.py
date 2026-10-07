"""Persistent subscription, idempotency and delivery ownership contracts."""
import json

import pytest

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.codex_results import record_codex_turn_result


HOOK = {'key': 'fanxiu', 'account_id': 'wxid_robot', 'chat_id': '123@chatroom'}


@pytest.fixture
def store(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook(**{'key': HOOK['key'], 'account_id': HOOK['account_id'], 'chat_id': HOOK['chat_id']})
    store.subscribe_codex_thread('thread', HOOK['key'], [{'turn_id': 'old'}])
    return store


def enqueue(store, turn='turn'):
    return record_codex_turn_result({'thread_id': 'thread', 'turn_id': turn, 'text': '完成'}, store=store)


def test_business_stage_can_queue_before_turn_completion(store):
    from backend.core.messaging.codex_results import record_codex_stage_result
    assert record_codex_stage_result('thread', 'attempt-100:receipt', '实际完成102次', store=store)
    assert not record_codex_stage_result('thread', 'attempt-100:receipt', '实际完成102次', store=store)
    assert not record_codex_stage_result('unsubscribed', 'attempt-100', '完成', store=store)
    assert len(store.pending_codex_notifications()) == 1
    with pytest.raises(ValueError):
        record_codex_stage_result('thread', '', '完成', store=store)


def test_incremental_reporting_has_one_durable_source(store):
    from backend.core.messaging.codex_results import configure_codex_reporting, record_codex_stage_result
    assert configure_codex_reporting('thread', mode='stages_only', store=store) == {'mode': 'stages_only'}
    assert record_codex_stage_result('thread', 'building:20261007:reentry', '重入无新增消耗', store=store)
    reopened = AgentStore(store.path)
    assert not record_codex_turn_result({'thread_id': 'thread', 'turn_id': 'final',
                                       'text': '重入验收已完成，未来义务保留'}, store=reopened)
    assert not record_codex_turn_result({'thread_id': 'thread', 'turn_id': 'wait',
                                       'text': '等待窗口，Goal active'}, store=reopened)
    assert not record_codex_stage_result('thread', 'building:20261007:reentry', '再次复核', store=reopened)
    assert record_codex_stage_result('thread', 'pet:20261007:consumption', '新增实际消费', store=reopened)
    assert len(reopened.pending_codex_notifications()) == 2
    configure_codex_reporting('thread', mode='turns_and_stages', store=reopened)
    assert enqueue(reopened, 'next-final')
    with pytest.raises(ValueError):
        configure_codex_reporting('unknown', mode='stages_only', store=reopened)
    with pytest.raises(ValueError):
        configure_codex_reporting('thread', mode='silent-all', store=reopened)


def test_owner_ack_uses_group_answer_ledger_but_new_batches_remain(store):
    from backend.core.messaging.codex_results import record_codex_stage_result
    store.update_hook(HOOK['key'], consumed_seq=1327)
    assert not record_codex_stage_result('thread', 'xutian-owner1327-deadline21-accepted', '收到', store=store)
    assert not record_codex_stage_result('thread', 'new-ack-id', '收到', question_seqs=[1327], store=store)
    assert record_codex_stage_result('thread', 'xutian-batch-fresh', '新增真实收益', store=store)


def test_subscription_filters_replay_and_preserves_destination(store):
    assert not enqueue(store, 'old')
    assert enqueue(store)
    assert not enqueue(store)
    assert not store.enqueue_codex_completion('other', 'turn', '秘密')
    store.ensure_hook('other', HOOK['account_id'], '456@chatroom')
    with pytest.raises(ValueError, match='recipient'):
        store.subscribe_codex_thread('thread', 'other', [])
    store.subscribe_codex_thread('thread', 'fanxiu', [], enabled=False)
    assert not enqueue(store, 'disabled')
    assert store.pending_codex_notifications() == []
    store.subscribe_codex_thread('thread', 'fanxiu', [], enabled=True)
    assert len(store.pending_codex_notifications()) == 1


def test_engine_delivery_never_consumes_group_questions(store):
    enqueue(store)
    notification = store.pending_codex_notifications()[0]
    store.update_hook('fanxiu', consumed_seq=8, summary='尚有问题', status='running')
    assert store.reserve_codex_notification(notification['id']) is None
    store.update_hook('fanxiu', status='idle')
    calls = []
    plan = prepare_reply(notification['text'], [], private=False)
    def sender(*args, **kwargs):
        calls.append((args, kwargs))
        return {'result': 1}
    assert deliver_reply(store, HOOK, 0, notification['text'], plan,
                         notification_id=notification['id'], sender=sender) == 'sent'
    assert store.hook('fanxiu')['consumed_seq'] == 8
    assert store.hook('fanxiu')['summary'] == '尚有问题'
    assert calls[0][0][0] == HOOK['chat_id']
    assert calls[0][1]['sender_account_id'] == HOOK['account_id']
    assert not enqueue(store)
    assert store.pending_codex_notifications() == []
    assert store.codex_notifications_status()['notifications'][0]['status'] == 'sent'


def test_crashed_send_is_frozen_and_explicit_reconciliation_recovers(store):
    enqueue(store)
    notification = store.pending_codex_notifications()[0]
    reply_id = store.reserve_codex_notification(notification['id'])
    store.recover()
    assert store.codex_notifications_status()['notifications'][0]['status'] == 'uncertain'
    assert not enqueue(store)
    assert store.pending_codex_notifications() == []
    store.reconcile_reply(reply_id, sent=False, evidence='正式发送未提交，已核验')
    assert store.reserve_codex_notification(notification['id']) == reply_id
    store.finish_reply(reply_id, 'uncertain', {'error': '发送结果不明'})
    store.reconcile_reply(reply_id, sent=True, evidence='发送回读确认')
    assert store.hook('fanxiu')['status'] == 'idle'
    assert store.hook('fanxiu')['consumed_seq'] == 0


def test_hook_result_cannot_trigger_attachment_send(store):
    assert record_codex_turn_result({'session_id': 'thread', 'turn_id': 'file',
        'last_assistant_message': 'CODECLAW_FILE: C:/secret.txt'}, store=store)
    row = store.pending_codex_notifications()[0]
    assert all(kind == 'text' for kind, text in prepare_reply(row['text'], [], private=False).parts)
    assert not record_codex_turn_result({'session_id': 'thread', 'turn_id': 'more',
        'stop_hook_active': True, 'last_assistant_message': 'continue'}, store=store)


def test_authorized_question_image_requires_evidence_directory(store, tmp_path, monkeypatch):
    from PIL import Image
    from backend.core import temp_paths
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    monkeypatch.setattr(temp_paths, 'codeyun_temp_root', lambda name: evidence)
    screenshot = evidence / 'current.png'
    Image.new('RGB', (10, 10)).save(screenshot)
    store.set_meta('codex_result_images:thread', True)
    assert record_codex_turn_result({'thread_id': 'thread', 'turn_id': 'question',
        'text': '请确认当前目标\nCODECLAW_IMAGE: ' + str(screenshot)}, store=store)
    row = store.pending_codex_notifications()[0]
    assert prepare_reply(row['text'], [], private=False).parts[-1] == ('image', screenshot)
    other = tmp_path / 'unrelated.png'
    Image.new('RGB', (10, 10)).save(other)
    with pytest.raises(ValueError, match='临时目录'):
        record_codex_turn_result({'thread_id': 'thread', 'turn_id': 'outside',
            'text': 'CODECLAW_IMAGE: ' + str(other)}, store=store)


def test_provider_requires_native_completed_event(tmp_path, monkeypatch):
    from backend.core.codex import sessions
    path = tmp_path / 'rollout.jsonl'
    entries = [
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'phase': 'final_answer'}},
        {'type': 'event_msg', 'timestamp': 'now', 'payload': {'type': 'task_complete', 'turn_id': 'native', 'last_agent_message': '结果'}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'last_agent_message': '无ID'}},
    ]
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n{"truncated"', encoding='utf-8')
    monkeypatch.setattr(sessions, '_resolve_thread_record', lambda *args: ({}, {'rollout_path': str(path)}))
    assert sessions.read_codex_completed_turns('thread') == [
        {'thread_id': 'thread', 'turn_id': 'native', 'text': '结果', 'timestamp': 'now'}]


def test_worker_collects_once_and_verifies_archive_echo(store, monkeypatch):
    import time
    import threading
    from types import SimpleNamespace
    from backend.core.messaging import codex_results
    monkeypatch.setattr(codex_results, 'read_codex_completed_turns', lambda thread: [
        {'thread_id': thread, 'turn_id': 'fresh', 'text': '实际阶段结果'}])
    echoes = []
    def sender(chat, text, *, sender_account_id):
        echoes.append({'local_id': len(echoes)+1, 'message_text': text,
                       'sender_username': sender_account_id, 'create_time': time.time()})
        return {'result': 1}
    service = SimpleNamespace(store=store, config={'hooks': [HOOK]}, stop_event=threading.Event(),
        poll_once=lambda: None, sender=sender, image_sender=None, file_sender=None,
        source=lambda account: SimpleNamespace(list_messages=lambda *args, **kwargs: {'items': echoes}))
    codex_results.sync_codex_results(service)
    codex_results.sync_codex_results(service)
    assert len(echoes) == 1
    row = store.codex_notifications_status()['notifications'][0]
    assert row['status'] == 'sent'
    assert store.get_meta(f"codex_result_verification:{row['id']}")['status'] == 'verified'
