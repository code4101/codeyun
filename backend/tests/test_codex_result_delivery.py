"""Persistent subscription, idempotency and delivery ownership contracts."""
import json

import pytest

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.codex_results import record_codex_turn_result


HOOK = {'key': 'fanxiu', 'account_id': 'wxid_robot', 'chat_id': '123@chatroom'}


@pytest.mark.parametrize('sender,created,text,expected', [
    ('wxid_robot', 101, '首领真实回执', 'verified'),
    ('other', 101, '首领真实回执', 'awaiting_archive'),
    ('wxid_robot', 96, '首领真实回执', 'awaiting_archive'),
    ('wxid_robot', 101, '其他回执', 'awaiting_archive'),
])
def test_archive_decoded_recent_fallback_keeps_sender_time_text_gates(
        sender, created, text, expected):
    from backend.core.messaging.codex_results import read_codex_notification_archive

    class Archive:
        def __init__(self):
            self.calls = []

        def list_messages(self, chat, **kwargs):
            self.calls.append((chat, kwargs))
            # A provider's raw-content search misses compressed text, while its
            # public listing returns the decoded message.
            return {'items': [] if 'q' in kwargs else [dict(
                local_id=323, sender_username=sender, create_time=created,
                message_text=text)]}

    archive = Archive()
    result = read_codex_notification_archive(
        dict(status='sent', text='首领真实回执', created_at=100),
        hook=HOOK, source=archive)
    assert result['status'] == expected
    assert result['local_ids'] == ([323] if expected == 'verified' else [])
    assert len(archive.calls) == 2
    assert all(call[1]['limit'] == 100 and
               call[1]['include_resources'] is False for call in archive.calls)


def test_archive_multipart_fallback_reads_recent_messages_once():
    from backend.core.messaging.codex_results import read_codex_notification_archive
    text = '甲' * 120 + '\n' + '乙' * 120
    parts = [part for kind, part in prepare_reply(
        text, [], private=False, limit=100).parts if kind == 'text']
    assert len(parts) > 1
    calls = []

    class Archive:
        def list_messages(self, chat, **kwargs):
            calls.append(kwargs)
            return {'items': [] if 'q' in kwargs else [dict(
                local_id=index, sender_username=HOOK['account_id'],
                create_time=101, message_text=part)
                for index, part in enumerate(parts, 1)]}

    result = read_codex_notification_archive(
        dict(status='sent', text=text, created_at=100), hook=HOOK,
        source=Archive(), reply_max_chars=100)
    assert result['status'] == 'verified'
    assert sum('q' not in call for call in calls) == 1


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
    from backend.core.codex import app_server
    monkeypatch.setattr(app_server, 'read_codex_thread_metadata', lambda thread: {'id': thread, 'path': str(path)})
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


def test_stages_only_worker_delivers_without_native_history(store, monkeypatch):
    import threading
    import time
    from types import SimpleNamespace
    from backend.core.messaging import codex_results

    codex_results.configure_codex_reporting('thread', mode='stages_only', store=store)
    codex_results.record_codex_stage_result('thread', 'receipt', '实际完成', store=store)

    def unreadable_history(thread):
        raise OSError('历史轮次暂不可读')

    monkeypatch.setattr(codex_results, 'read_codex_completed_turns', unreadable_history)
    echoes = []

    def sender(chat, text, *, sender_account_id):
        echoes.append({'local_id': 1, 'message_text': text,
                       'sender_username': sender_account_id, 'create_time': time.time()})
        return {'result': 1}

    service = SimpleNamespace(store=store, config={'hooks': [HOOK]}, stop_event=threading.Event(),
        poll_once=lambda: None, sender=sender, image_sender=None, file_sender=None,
        source=lambda account: SimpleNamespace(list_messages=lambda *args, **kwargs: {'items': echoes}))
    codex_results.sync_codex_results(service)
    codex_results.sync_codex_results(service)
    assert len(echoes) == 1
    notification = store.codex_notifications_status()['notifications'][0]
    assert notification['status'] == 'sent'
    assert store.get_meta(f"codex_result_verification:{notification['id']}")['status'] == 'verified'

@pytest.mark.parametrize('failure_phase,error_name', [
    ('read', name) for name in ['CodexCacheRefreshBusyError', 'CodexCacheSourceChangedError',
                              'RuntimeError', 'TimeoutError', 'FileNotFoundError', 'PermissionError', 'ValueError']
] + [('record', 'ValueError')])
def test_deferred_discovery_keeps_stage_delivery_archive_readback_and_native_cursor(store,monkeypatch,error_name,failure_phase):
    import threading
    import time
    from types import SimpleNamespace
    from backend.core.codex import sessions
    from backend.core.messaging import codex_results

    # A genuinely persisted sent receipt has no initial archive echo. Discovery
    # must never resend it while later sweeps wait for/read that archive echo.
    enqueue(store,'already-sent')
    sent=store.pending_codex_notifications()[0]
    archive=[];calls=[];visible=[]
    def sender(chat,text,*,sender_account_id):
        calls.append(text)
        archive.append({'local_id':len(archive)+1,'message_text':text,
            'sender_username':sender_account_id,'create_time':time.time()})
        return {'result':1}
    deliver_reply(store,HOOK,0,sent['text'],prepare_reply(sent['text'],[],private=False),
                  notification_id=sent['id'],sender=sender)
    assert len(calls)==1
    store.subscribe_codex_thread('other',HOOK['key'],[])
    codex_results.record_codex_stage_result('thread','accepted-stage','正式阶段凭证',store=store)
    store.update_hook(HOOK['key'],consumed_seq=42)
    subscriptions=store.codex_notifications_status()['subscriptions']
    deferred=True;reads=[]
    import builtins
    error_type=getattr(sessions,error_name,None) or getattr(builtins,error_name)
    def completions(thread):
        reads.append(thread)
        if thread=='thread' and deferred and failure_phase=='read':raise error_type('source temporarily unavailable')
        return [{'thread_id':thread,'turn_id':'native-'+thread,'text':'原生完成-'+thread}]
    monkeypatch.setattr(codex_results,'read_codex_completed_turns',completions)
    original_record=codex_results.record_codex_turn_result
    def record(turn, **kwargs):
        if turn['thread_id']=='thread' and deferred and failure_phase=='record':
            raise error_type('source temporarily unavailable')
        return original_record(turn, **kwargs)
    monkeypatch.setattr(codex_results,'record_codex_turn_result',record)
    service=SimpleNamespace(store=AgentStore(store.path),config={'hooks':[HOOK]},
        stop_event=threading.Event(),poll_once=lambda:None,sender=sender,image_sender=None,file_sender=None,
        source=lambda account:SimpleNamespace(list_messages=lambda *a,**kw:{'items':visible}))
    codex_results.sync_codex_results(service)
    reopened=AgentStore(store.path)
    notifications=reopened.codex_notifications_status()['notifications']
    assert len(calls)==3  # pre-sent + stage + healthy other subscription
    assert all(row['status']=='sent' for row in notifications if row['status']!='baseline')
    assert not any(row['turn_id']=='native-thread' for row in notifications)
    assert reopened.codex_notifications_status()['subscriptions']==subscriptions
    assert reopened.hook(HOOK['key'])['consumed_seq']==42
    marker='codex_result_discovery:thread'
    diagnostic=reopened.get_meta(marker)
    assert diagnostic['status']=='deferred' and diagnostic['code']==getattr(error_type,'code','completion_discovery_failed')
    assert diagnostic['error_type']==error_name
    assert diagnostic['error']=='source temporarily unavailable'
    assert diagnostic['phase']==('read_native_completions' if failure_phase=='read' else 'record_native_completions')
    health=reopened.get_meta('codex_results_worker')
    assert health['error'] and health['deferred_subscriptions']==[diagnostic]
    verify='codex_result_verification:'+str(sent['id'])
    assert reopened.get_meta(verify)['status']=='awaiting_archive'

    visible.extend(archive)
    service.store=reopened
    codex_results.sync_codex_results(service)
    assert len(calls)==3 and reopened.get_meta(verify)['status']=='verified'
    for row in reopened.codex_notifications_status()['notifications']:
        if row['status']=='sent':
            assert reopened.get_meta('codex_result_verification:'+str(row['id']))['status']=='verified'
    assert reopened.get_meta('codex_results_worker')['error']

    deferred=False
    codex_results.sync_codex_results(service)
    assert len(calls)==4
    assert reopened.get_meta(marker)['status']=='success'
    assert reopened.get_meta('codex_results_worker')['error'] is None
    assert reopened.get_meta('codex_results_worker')['deferred_subscriptions']==[]
    codex_results.sync_codex_results(service)
    assert len(calls)==4
    assert sum(row['turn_id']=='native-thread' for row in reopened.codex_notifications_status()['notifications'])==1


@pytest.mark.parametrize('error',[KeyboardInterrupt('cancel'),SystemExit('exit')])
def test_discovery_interrupts_propagate_and_do_not_report_clean_worker(store,monkeypatch,error):
    import threading
    from types import SimpleNamespace
    from backend.core.messaging import codex_results
    enqueue(store)
    store.set_meta('codex_results_worker',{'error':'earlier worker failure'})
    def read(thread):raise error
    monkeypatch.setattr(codex_results,'read_codex_completed_turns',read)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected discovery fault was silently bypassed')
    service=SimpleNamespace(store=store,config={'hooks':[HOOK]},stop_event=threading.Event(),
        poll_once=forbidden,sender=forbidden,image_sender=None,file_sender=None,source=forbidden)
    with pytest.raises(type(error),match=str(error)):codex_results.sync_codex_results(service)
    assert store.get_meta('codex_results_worker')['error']=='earlier worker failure'
    assert store.get_meta('codex_result_discovery:thread') is None
    assert len(store.pending_codex_notifications())==1
