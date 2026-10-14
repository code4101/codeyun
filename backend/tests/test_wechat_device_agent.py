import json
import threading
from types import SimpleNamespace

import pytest

from backend.core.messaging.wechat_device_agent import process_device_agent_hook
from backend.core.messaging.wechat_agent import WechatAgentService
from backend.tests.test_wechat_message_routing import fixture_store, ingest, HOOK

REMOTE_HOOK = {**HOOK, 'processor': 'device_agent', 'device_entry_id': 'remote-entry',
               'domain': 'general', 'message_routing': True, 'name': 'CodeHub'}


class Remote:
    def __init__(self):
        self.calls = []
        self.turns = []
        self.title = None
        self.status = 'pending'
        self.timeout = False
        self.answer = None

    def validate_target(self):
        return {'enabled': True}

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if method == 'POST':
            body = kwargs['json']
            self.title = body.get('title', self.title)
            turn = {'id': f't{len(self.turns)}', 'context': body['context']}
            self.turns.append(turn)
            self.status = 'pending'
            if self.timeout:
                raise TimeoutError('response lost after acceptance')
            return {'id': 'session' if path == '/sessions' else turn['id'], 'last_turn_id': turn['id']}
        if path == '/sessions':
            return {'items': [{'id': 'session', 'title': self.title}]}
        if path == '/sessions/session':
            return {'id': 'session', 'turns': self.turns}
        seqs = [m['seq'] for m in self.turns[-1]['context']['messages']]
        return {'status': self.status, 'result_report': {'final_message': json.dumps(
            self.answer or {'action': 'reply', 'text': 'CodeHub设备检查完成', 'question_seqs': seqs})}}


def setup(tmp_path):
    store = fixture_store(tmp_path)
    sent = []
    service = SimpleNamespace(store=store, config={}, workers=[], last_error=None, stop_event=threading.Event(),
                              poll_once=lambda: None, sender=lambda *a, **kw: sent.append(a) or {})
    return service, Remote(), sent


def step(service, remote):
    return process_device_agent_hook(service, REMOTE_HOOK, remote)


def test_remote_execution_and_reply_without_local_client(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查你的设备', ['bot'])
    assert step(service, remote)
    assert remote.calls[0][0] == 'POST'
    assert not step(service, remote)
    remote.status = 'completed'
    assert step(service, remote)
    assert len(sent) == 1
    assert not service.store.pending_messages('group')
    # Provider isolation: creating this hook's client must never construct Codex.
    svc = WechatAgentService({'accounts': [], 'hooks': []}, store=service.store,
                             client_factory=lambda: pytest.fail('local model constructed'))
    assert svc.new_client(REMOTE_HOOK).entry_id == 'remote-entry'


def test_lost_post_response_reconciles_without_resubmitting(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '操作设备', ['bot'])
    remote.timeout = True
    with pytest.raises(TimeoutError):
        step(service, remote)
    # A new worker with the same durable store resumes the remote turn.
    assert not step(service, remote)
    remote.status = 'completed'
    assert step(service, remote)
    assert len([c for c in remote.calls if c[0] == 'POST']) == 1
    assert len(sent) == 1


def test_correction_waits_for_busy_remote_then_suppresses_old_answer(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查设备', ['bot'])
    step(service, remote)
    ingest(service.store, 2, '改为检查浏览器')
    assert not step(service, remote)
    assert len(remote.turns) == 1
    remote.status = 'completed'
    assert step(service, remote)
    assert not sent
    assert remote.calls[-1][1] == '/sessions/session/turns'
    assert [m['seq'] for m in remote.turns[-1]['context']['messages']] == [1, 2]
    remote.status = 'completed'
    assert step(service, remote)
    assert len(sent) == 1


def test_terminal_failure_never_replays_action(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '修复设备', ['bot'])
    step(service, remote)
    remote.status = 'failed'
    for _ in range(2):
        with pytest.raises(RuntimeError, match='执行失败'):
            step(service, remote)
    assert len(remote.turns) == 1
    assert not sent
    assert service.store.pending_messages('group')


def test_partial_reply_retains_other_question(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '查询设备', ['bot'])
    ingest(service.store, 2, '查询浏览器', ['bot'])
    step(service, remote)
    remote.status = 'completed'
    remote.answer = {'action': 'reply', 'text': '设备正常', 'question_seqs': [1]}
    assert step(service, remote)
    assert [m['seq'] for m in service.store.pending_messages('group')] == [2]


def test_unexplained_image_and_unwoken_chat_do_not_post(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '真人聊天')
    assert not step(service, remote)
    service.store.ingest('bot', {'events': [{'message_id': 'photo', 'chat_id': '1@chatroom',
        'sender_id': 'player', 'timestamp': 1, 'text': '<img/>', 'message_type': 3, 'mentions': ['bot']}], 'cursor': {}})
    assert not step(service, remote)
    assert not remote.calls


def test_uncertain_post_without_remote_receipt_never_retries(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '操作设备', ['bot'])
    remote.timeout = True
    with pytest.raises(TimeoutError):
        step(service, remote)
    remote.title = 'different request'
    for _ in range(2):
        with pytest.raises(RuntimeError, match='受理状态未确认'):
            step(service, remote)
    assert len(remote.turns) == 1
    assert not sent


def test_crash_after_delivery_does_not_send_twice(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查设备', ['bot'])
    step(service, remote)
    remote.status = 'completed'
    original = service.store.set_meta
    def crash_on_clear(key, value):
        if key == 'remote_device_request:group' and value == {}:
            raise RuntimeError('crash after delivery')
        return original(key, value)
    service.store.set_meta = crash_on_clear
    with pytest.raises(RuntimeError, match='crash after delivery'):
        step(service, remote)
    service.store.set_meta = original
    assert step(service, remote)
    assert len(sent) == 1
    assert len(remote.turns) == 1


def test_followup_reuses_remote_session(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查设备', ['bot'])
    step(service, remote)
    remote.status = 'completed'
    step(service, remote)
    ingest(service.store, 2, '现在检查浏览器', ['bot'])
    assert step(service, remote)
    assert remote.calls[-1][1] == '/sessions/session/turns'


@pytest.mark.parametrize('manifest', [
    {'device_id': 'local-device', 'enabled': True},
    {'device_id': 'remote-device', 'enabled': False},
])
def test_remote_endpoint_identity_and_enabled_state_are_verified(manifest):
    from backend.core.devices.agent_client import RemoteDeviceAgentClient
    client = RemoteDeviceAgentClient('remote-entry')
    client.connection = lambda: ('url', {}, 'remote-device')
    client.request = lambda *a, **kw: manifest
    with pytest.raises(ValueError, match='身份不匹配或灵网接待未开启'):
        client.validate_target()


def test_target_preflight_failure_leaves_no_post_intent(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查设备', ['bot'])
    def unavailable():
        raise ValueError('device offline')
    remote.validate_target = unavailable
    with pytest.raises(ValueError):
        step(service, remote)
    assert not service.store.get_meta('remote_device_request:group')
    assert not remote.calls
