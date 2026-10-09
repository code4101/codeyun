from types import SimpleNamespace
import pytest

from backend.tests.test_wechat_message_routing import fixture_store, ingest, HOOK
from backend.core.messaging.wechat_flash_routing import process_flash_routes


def test_chatter_never_calls_any_model_and_mention_keeps_original(tmp_path):
    store = fixture_store(tmp_path)
    for seq in range(1, 101):
        ingest(store, seq, '真人之间的聊天')
    ingest(store, 101, '@机器人 请查询资料', ['bot'])
    calls = []
    svc = SimpleNamespace(store=store, last_error=None, workers=[])
    def classifier(hook, message):
        calls.append(message['seq'])
        return {'kind':'research', 'intent':'查询资料'}
    assert process_flash_routes(svc, HOOK, classifier=classifier)
    assert calls == [101]
    assert [m['seq'] for m in store.pending_messages('group')] == [101]
    assert store.pending_messages('group')[0]['text'] == '@机器人 请查询资料'
    assert len(store.recent('group', 100, limit=100)) == 100
    resumed = fixture_store(tmp_path)
    svc.store = resumed
    assert not process_flash_routes(svc, HOOK, classifier=classifier)
    assert calls == [101]


def test_flash_failure_keeps_explicit_request_and_ignoring_cannot_steal_work(tmp_path):
    store = fixture_store(tmp_path)
    ingest(store,1,'请处理',['bot'])
    svc = SimpleNamespace(store=store,last_error=None,workers=[])
    def failure(*args):
        raise TimeoutError('offline')
    assert process_flash_routes(svc,HOOK,classifier=failure)
    assert [m['seq'] for m in store.pending_messages('group')] == [1]
    store.ignore_unaddressed_message('group',1)
    assert [m['seq'] for m in store.pending_messages('group')] == [1]
    with pytest.raises(ValueError,match='Foreign'):
        store.ignore_unaddressed_message('group',999)


def test_photo_is_kept_until_later_explanation(tmp_path):
    store = fixture_store(tmp_path)
    store.ingest('bot', {'events':[{'message_id':'photo','chat_id':'1@chatroom','sender_id':'player',
        'timestamp':1,'text':'<msg><img /></msg>','message_type':3}], 'cursor':{}})
    svc = SimpleNamespace(store=store,last_error=None,workers=[])
    assert process_flash_routes(svc,HOOK,classifier=lambda *a:pytest.fail('photo cannot classify'))
    assert [m['message_type'] for m in store.pending_messages('group')] == [3]


def test_followup_window_uses_context_and_only_related_input_extends_it(tmp_path):
    import time
    store = fixture_store(tmp_path)
    store.update_hook('group',thread_id='daily',awake_until=time.time()+300)
    ingest(store,1,'补充：只查北京地区')
    ingest(store,2,'我们晚上去哪吃饭？')
    svc=SimpleNamespace(store=store,last_error=None,workers=[])
    def classifier(hook,message):
        assert not hook['_routing_context']['explicit_mention']
        assert hook['_routing_context']['recent_context']
        return {'kind':'research','intent':message['text'],'engage':message['seq']==1}
    assert process_flash_routes(svc,HOOK,classifier=classifier)
    deadline=store.hook('group')['awake_until']
    assert process_flash_routes(svc,HOOK,classifier=classifier)
    assert store.hook('group')['awake_until']==deadline
    assert [m['seq'] for m in store.pending_messages('group')]==[1]
    store.update_hook('group',awake_until=time.time()-1)
    ingest(store,3,'又一条未@消息')
    assert process_flash_routes(svc,HOOK,classifier=lambda *a:pytest.fail('expired window'))
    assert [m['seq'] for m in store.pending_messages('group')]==[1]


def test_semantic_mode_classifies_expired_window_without_waking_chatter(tmp_path):
    import time
    store = fixture_store(tmp_path)
    store.update_hook('group', thread_id='daily', awake_until=time.time()-300)
    ingest(store,1,'重置了啥？')
    ingest(store,2,'我们晚上去哪吃饭？')
    hook = {**HOOK, 'router_mode':'flash_semantic'}
    svc = SimpleNamespace(store=store,last_error=None,workers=[])
    calls = []
    def classifier(hook,message):
        context = hook['_routing_context']
        assert context['semantic_attention']
        calls.append((message['seq'], context['strong_context']))
        return {'kind':'question','intent':'追问通知' if message['seq']==1 else '真人聊天','engage':message['seq']==1}
    assert process_flash_routes(svc,hook,classifier=classifier)
    deadline = store.hook('group')['awake_until']
    assert process_flash_routes(svc,hook,classifier=classifier)
    assert calls == [(1,False),(2,True)]
    assert store.hook('group')['awake_until'] == deadline
    assert [m['seq'] for m in store.pending_messages('group')] == [1]
    assert not process_flash_routes(svc,hook,classifier=classifier)


def test_semantic_failure_retains_unmentioned_message_for_retry(tmp_path):
    store = fixture_store(tmp_path)
    ingest(store,1,'重置了啥？')
    svc = SimpleNamespace(store=store,last_error=None,workers=[])
    def failure(*args):
        raise TimeoutError('offline')
    assert not process_flash_routes(svc,{**HOOK,'router_mode':'flash_semantic'},classifier=failure)
    assert store.pending_messages('group',all_lanes=True)[0]['seq'] == 1
    assert not store.message_routes('group')


def test_opencode_classifier_uses_denied_tool_public_wrapper(monkeypatch):
    from backend.core.messaging.wechat_flash_routing import flash_intent
    from backend.core import note_preparation_research as cli
    calls = []
    def invoke(prompt, **kwargs):
        calls.append((prompt,kwargs))
        return {'text':'{"kind":"question","intent":"追问机器人通知","engage":true}',
                'resource':'ds','provider':'opencode-go','model':'deepseek-v4.1-flash'}
    monkeypatch.setattr(cli,'invoke',invoke)
    intent = flash_intent({**HOOK,'router_provider':'opencode_go'},
                         {'seq':1,'sender_id':'player','text':'重置了啥？','message_type':1})
    assert intent['engage'] is True
    assert intent['resource'] == 'ds'
    assert intent['provider'] == 'opencode-go'
    assert intent['model'] == 'deepseek-v4.1-flash'
    assert calls[0][1]['web'] is False and calls[0][1]['timeout'] == 45
