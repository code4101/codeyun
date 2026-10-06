import copy
import threading
from types import SimpleNamespace

import pytest

from backend.core.messaging.wechat_agent import ATTENDANCE_ACCOUNT, OWNER_ACCOUNT, WechatAgentService
from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging import wechat_agent as agent
from backend.core.messaging.wechat_group_registration import registration_scope, register_group, process_group_registrations


def event(identity='create', **changes):
    return {'message_id':identity, 'chat_id':'12345@chatroom', 'sender_id':OWNER_ACCOUNT,
        'timestamp':1, 'message_type':1, 'text':'@考勤机器人\u2005请创建这个群的机器人功能，这是专门开发 projectgraph 功能的', **changes}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = AgentStore(tmp_path/'events.sqlite')
    config = {'enabled':True, 'accounts':[ATTENDANCE_ACCOUNT], 'hooks':[]}
    monkeypatch.setattr(agent, 'load_config', lambda:copy.deepcopy(config))
    def save(value, **kwargs):
        assert kwargs['expected_config']==config
        config.clear(); config.update(copy.deepcopy(value))
        return value
    monkeypatch.setattr(agent, 'save_config', save)
    source = SimpleNamespace(list_chats=lambda **kwargs:[{'username':'12345@chatroom','name':'pgragh 流程图'}])
    return store, config, source


@pytest.mark.parametrize('changes', [
    {'sender_id':'ordinary-member'}, {'chat_id':OWNER_ACCOUNT}, {'message_type':3}, {'message_type':49},
    {'text':'请创建这个群的机器人功能，这是专门开发 projectgraph 功能的'},
    {'text':'@其他机器人 请创建这个群的机器人功能，这是专门开发 projectgraph 功能的'},
    {'text':'@考勤机器人 怎样创建这个群的机器人功能？'},
    {'text':'@考勤机器人 请创建这个群的机器人'},
])
def test_only_owner_explicit_at_text_and_scope_can_register(changes):
    assert registration_scope(event(**changes), ATTENDANCE_ACCOUNT) is None


def test_native_mention_and_provider_sender_prefix_are_supported():
    e = event(text=OWNER_ACCOUNT+':\n'+event()['text'])
    assert 'projectgraph' in registration_scope(e, ATTENDANCE_ACCOUNT)
    e = event(text='请创建本群机器人，负责产品讨论和研发', mentions=[ATTENDANCE_ACCOUNT])
    assert registration_scope(e, ATTENDANCE_ACCOUNT)=='负责产品讨论和研发'


def test_creation_survives_restart_without_replaying_older_history(setup):
    store, config, source = setup
    store.ingest(ATTENDANCE_ACCOUNT, {'events':[event('old', text='旧聊天'), event(), event('followup',text='还有补充')], 'cursor':{}})
    result = register_group(event(), ATTENDANCE_ACCOUNT, source, store)
    hook = result['hook']
    assert result['created'] and hook['domain']=='general'
    assert 'projectgraph' in hook['description']
    assert [m['message_id'] for m in store.pending_messages(hook['key'], store.hook(hook['key'])['consumed_seq'])]==['create','followup']
    reopened = AgentStore(store.path)
    WechatAgentService(config, store=reopened, source_factory=lambda _:source)
    assert [m['message_id'] for m in reopened.pending_messages(hook['key'], reopened.hook(hook['key'])['consumed_seq'])]==['create','followup']
    assert not register_group(event(), ATTENDANCE_ACCOUNT, source, reopened)['created']
    assert len(config['hooks'])==1


def test_durable_admission_retries_after_config_save_failure(setup, monkeypatch):
    store, config, source = setup
    store.set_meta('group_registration_cursor:'+ATTENDANCE_ACCOUNT, 0)
    store.ingest(ATTENDANCE_ACCOUNT, {'events':[event()], 'cursor':{}})
    svc = SimpleNamespace(store=store, config=copy.deepcopy(config), source=lambda _:source, workers=[])
    save = agent.save_config
    monkeypatch.setattr(agent, 'save_config', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('write failed')))
    with pytest.raises(RuntimeError, match='write failed'):
        process_group_registrations(svc, ATTENDANCE_ACCOUNT)
    assert store.get_meta('group_registration_cursor:'+ATTENDANCE_ACCOUNT)==0
    monkeypatch.setattr(agent, 'save_config', save)
    assert process_group_registrations(svc, ATTENDANCE_ACCOUNT)==['group-12345']
    assert process_group_registrations(svc, ATTENDANCE_ACCOUNT)==[]
    assert len(config['hooks'])==1


def test_config_published_before_crash_can_finish_registration(setup, monkeypatch):
    store, config, source = setup
    store.ingest(ATTENDANCE_ACCOUNT, {'events':[event()], 'cursor':{}})
    ensure = store.ensure_hook
    monkeypatch.setattr(store, 'ensure_hook', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError, match='crash'):
        register_group(event(), ATTENDANCE_ACCOUNT, source, store)
    assert config['hooks'][0]['registration_message_id']=='create'
    monkeypatch.setattr(store, 'ensure_hook', ensure)
    hook = register_group(event(), ATTENDANCE_ACCOUNT, source, store)['hook']
    assert [m['message_id'] for m in store.pending_messages(hook['key'], store.hook(hook['key'])['consumed_seq'])]==['create']


def test_config_api_preserves_registration_anchor():
    from backend.api.wechat_archive import WechatAgentHookConfig
    hook = WechatAgentHookConfig(key='group-12345', account_id=ATTENDANCE_ACCOUNT,
        chat_id='12345@chatroom', domain='general', description='开发', registration_message_id='source-id')
    assert hook.model_dump()['registration_message_id']=='source-id'


def test_registration_failure_does_not_stop_existing_listener(setup):
    store, config, source = setup
    store.set_meta('group_registration_cursor:'+ATTENDANCE_ACCOUNT, 0)
    def list_chats(**kwargs):
        raise RuntimeError('group lookup temporarily unavailable')
    source.list_chats = list_chats
    source.poll_updates = lambda cursor: {'events':[event()], 'cursor':{'accepted':True}}
    svc = WechatAgentService(config, store=store, source_factory=lambda _:source)
    assert svc.poll_once()['new_events'][ATTENDANCE_ACCOUNT] == 1
    assert svc.last_error is None
    assert store.get_meta('group_registration_cursor:'+ATTENDANCE_ACCOUNT)==0
    assert store.get_meta('group_registration_error:'+ATTENDANCE_ACCOUNT)['error']=='group lookup temporarily unavailable'
