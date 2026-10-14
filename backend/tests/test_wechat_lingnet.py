import json

import pytest

from backend.core.messaging import wechat_lingnet as adapter
from backend.core.messaging.wechat_agent import WechatAgentService, save_config, ATTENDANCE_ACCOUNT
from backend.core.messaging.wechat_device_agent import process_device_agent_hook
from backend.tests.test_wechat_device_agent import REMOTE_HOOK, setup
from backend.tests.test_wechat_message_routing import ingest


HOOK = {**REMOTE_HOOK, 'processor': 'lingnet', 'lingnet_peer_id': 'peer-id'}
HOOK.pop('device_entry_id')


@pytest.mark.parametrize('wake_mode, forwarded', [('mentions', False), ('all_members', True)])
def test_unmentioned_member_can_start_only_in_open_relay(tmp_path, wake_mode, forwarded):
    service, remote, sent = setup(tmp_path)
    hook = {**HOOK, 'wake_mode': wake_mode}
    ingest(service.store, 1, '你好，帮我解释一下这个功能')
    assert process_device_agent_hook(service, hook, remote) == forwarded
    if forwarded:
        message = remote.turns[0]['context']['messages'][0]
        assert message['sender_id'] == 'player'
        assert message['verified_owner'] is False
        remote.status = 'completed'
        assert process_device_agent_hook(service, hook, remote)
        assert len(sent) == 1
    else:
        assert not remote.calls


def test_lingnet_relay_delivery_and_followup(tmp_path):
    service, remote, sent = setup(tmp_path)
    ingest(service.store, 1, '检查你的设备', ['bot'])
    assert process_device_agent_hook(service, HOOK, remote)
    assert service.store.get_meta('remote_device_request:group')['entry_id'] == 'lingnet:peer-id'
    remote.status = 'completed'
    assert process_device_agent_hook(service, HOOK, remote)
    assert len(sent) == 1
    ingest(service.store, 2, '继续检查', ['bot'])
    assert process_device_agent_hook(service, HOOK, remote)
    assert remote.calls[-1][1] == '/sessions/session/turns'
    svc = WechatAgentService({'accounts': [], 'hooks': []}, store=service.store,
                            client_factory=lambda: pytest.fail('local model constructed'))
    assert svc.new_client(HOOK).peer_id == 'peer-id'


def test_peer_credentials_and_identity(monkeypatch, tmp_path):
    from lingnet.config import ConfigStore
    store = ConfigStore(tmp_path)
    peer = store.add_peer(url='https://example.com/api/lingnet', token='secret', name='CodeHub')
    store.update_peer(peer['id'], {'node_id': 'remote-node'})
    monkeypatch.setattr(adapter, 'ConfigStore', lambda *_: store)
    calls = []
    def request(method, url, **kwargs):
        calls.append((url, kwargs['headers']['X-Device-Token']))
        return type('Response', (), {'ok': True, 'json': lambda self: {'device_id': 'remote-node', 'enabled': True}})()
    monkeypatch.setattr('backend.core.devices.agent_client.requests.request', request)
    client = adapter.LingnetWechatClient(peer['id'])
    assert client.validate_target()['enabled']
    store.update_peer(peer['id'], {'token': 'rotated'})
    client.request('GET', '/sessions')
    assert calls[-1][1] == 'rotated'
    with pytest.raises(ValueError, match='对端 ID'):
        adapter.LingnetWechatClient('CodeHub').connection()
    client.request = lambda *a, **k: {'device_id': 'wrong', 'enabled': True}
    with pytest.raises(ValueError, match='身份不匹配'):
        client.validate_target()


def test_config_keeps_lingnet_binding(monkeypatch, tmp_path):
    from backend.core.messaging import wechat_agent
    monkeypatch.setattr(wechat_agent, 'agent_root', lambda: tmp_path)
    hook = {**HOOK, 'account_id': ATTENDANCE_ACCOUNT, 'chat_id': '50493409880@chatroom',
            'description': 'CodeHub 微信中转', 'kind': 'group'}
    config = {'accounts': [ATTENDANCE_ACCOUNT], 'hooks': [hook]}
    save_config(config)
    assert json.loads((tmp_path / 'config.json').read_text(encoding='utf-8'))['hooks'][0]['lingnet_peer_id'] == 'peer-id'
    del hook['lingnet_peer_id']
    with pytest.raises(ValueError, match='relay'):
        save_config(config)
