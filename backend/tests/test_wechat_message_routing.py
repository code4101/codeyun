import threading
import time
from types import SimpleNamespace

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply
from backend.core.messaging.wechat_message_routing import process_message_routes

HOOK = {'key':'group','account_id':'bot','chat_id':'1@chatroom','domain':'general'}


def fixture_store(tmp_path):
    store = AgentStore(tmp_path / 'events.sqlite')
    store.ensure_hook('group','bot','1@chatroom')
    store.enable_message_routing('group')
    return store


def ingest(store,seq,text,mentions=None):
    store.ingest('bot',{'events':[{'message_id':str(seq),'chat_id':'1@chatroom','sender_id':'player',
        'timestamp':time.time(),'text':text,'message_type':1,'mentions':mentions or []}], 'cursor':{'n':seq}})


def test_disabling_router_restores_new_mentions_without_replaying_resolved(tmp_path):
    store = fixture_store(tmp_path)
    ingest(store, 1, 'unaddressed chatter')
    store.ignore_unaddressed_message('group', 1)
    ingest(store, 2, '@bot question', ['bot'])
    assert store.pending_messages('group') == []
    store.disable_message_routing('group')
    assert [m['seq'] for m in store.pending_messages('group')] == [2]
    restarted = AgentStore(store.path)
    assert [m['seq'] for m in restarted.pending_messages('group')] == [2]
    assert restarted.message_routes('group')[0]['lane'] == 'ignored'


def test_service_start_reconciles_disabled_routing_config(tmp_path):
    from backend.core.messaging.wechat_agent import WechatAgentService
    store = fixture_store(tmp_path)
    ingest(store, 1, '@bot question', ['bot'])
    WechatAgentService({'hooks': [{**HOOK, 'message_routing': False}]}, store=store)
    assert store.get_meta('message_routing:group')['enabled'] is False
    assert [m['seq'] for m in store.pending_messages('group')] == [1]


def test_quick_reply_does_not_interrupt_or_complete_busy_main(tmp_path):
    store = fixture_store(tmp_path)
    ingest(store,1,'做游戏任务')
    ingest(store,2,'你是什么模型',['bot'])
    store.record_message_route('group',1,'main')
    store.record_message_route('group',2,'reply',{'action':'reply','text':'模型X','question_seqs':[2]})
    store.update_hook('group',status='running',turn_id='main-turn')
    sent=[]
    plan=prepare_reply('模型X',[],private=False)
    assert deliver_reply(store,HOOK,2,'模型X',plan,independent_seq=2,independent_reviewed_through=2,
                         sender=lambda *a,**kw:sent.append(a) or {})=='sent'
    assert store.hook('group')['status']=='running'
    assert store.hook('group')['turn_id']=='main-turn'
    assert store.hook('group')['consumed_seq']==0
    assert [m['seq'] for m in store.pending_messages('group')]==[1]
    assert [m['seq'] for m in store.pending_messages('group',all_lanes=True)]==[1]
    assert len(sent)==1
    assert deliver_reply(store,HOOK,2,'模型X',plan,independent_seq=2,independent_reviewed_through=2,
                         sender=lambda *a,**kw:sent.append(a) or {})=='stale'
    store.resolve_routed_messages('group',[1])
    assert store.hook('group')['consumed_seq']==2


def test_main_completion_cannot_swallow_earlier_quick_question(tmp_path):
    store=fixture_store(tmp_path)
    ingest(store,1,'模型问题')
    ingest(store,2,'主线任务')
    store.record_message_route('group',1,'reply',{'text':'X'})
    store.record_message_route('group',2,'main')
    store.resolve_routed_messages('group',[2])
    assert store.hook('group')['consumed_seq']==0
    assert [m['seq'] for m in store.pending_messages('group',all_lanes=True)]==[1]


def test_restart_keeps_draft_and_new_correction_blocks_send(tmp_path):
    store=fixture_store(tmp_path)
    ingest(store,1,'模型问题')
    store.record_message_route('group',1,'reply',{'text':'X'})
    restarted=AgentStore(store.path)
    assert restarted.message_routes('group')[0]['lane']=='reply'
    ingest(store,2,'先别回答上一条')
    assert store.reserve_independent_reply('group',1,'X',reviewed_through=1) is None
    store.return_message_to_main('group',1)
    store.record_message_route('group',2,'main')
    assert [m['seq'] for m in store.pending_messages('group')]==[1,2]


def test_semantic_routing_separates_new_question_from_main_correction(tmp_path, monkeypatch):
    store=fixture_store(tmp_path)
    ingest(store,1,'你是什么模型',['bot'])
    ingest(store,2,'改选第2个',['bot'])
    import json
    from backend.core.ai import chat
    class Client:
        def __getattr__(self, name):
            raise AssertionError('Router accessed paid client')
    def local_chat(**kwargs):
        assert kwargs['provider_id'] == 'ollama'
        seq = json.loads(kwargs['messages'][0]['content'])['message']['seq']
        return {'content': json.dumps({'action':'reply' if seq==1 else 'defer', 'text':'model-X', 'question_seqs':[seq]})}
    monkeypatch.setattr(chat, 'chat_with_provider', local_chat)
    sent=[]
    svc=SimpleNamespace(store=store,workers=[],last_error=None,last_poll_at=None,config={},
        stop_event=threading.Event(),sender=lambda *a,**kw:sent.append(a) or {},poll_once=lambda:None)
    assert process_message_routes(svc,HOOK,Client())
    assert process_message_routes(svc,HOOK,Client())
    assert process_message_routes(svc,HOOK,Client())
    assert sent==[('1@chatroom','model-X')]
    assert [m['seq'] for m in store.pending_messages('group')]==[2]


def test_routing_enable_preserves_inputs_of_existing_active_writer(tmp_path):
    store=AgentStore(tmp_path/'events.sqlite')
    store.ensure_hook('group','bot','1@chatroom')
    ingest(store,1,'已提交操作')
    ingest(store,2,'新问题')
    store.set_meta('context_delivery:group',{'submitted_seq':1})
    store.enable_message_routing('group')
    assert [m['seq'] for m in store.pending_messages('group')]==[1]
    assert [m['seq'] for m in store.pending_messages('group',all_lanes=True)]==[1,2]


def test_flash_admission_uses_independent_answer_lane_while_main_busy(tmp_path, monkeypatch):
    store = fixture_store(tmp_path)
    ingest(store, 1, '正在执行的任务')
    store.record_message_route('group', 1, 'main')
    store.update_hook('group', status='running', turn_id='main-turn')
    ingest(store, 2, '什么是点云', ['bot'])
    ingest(store, 3, '把刚才任务改成第二项', ['bot'])
    import json
    from backend.core.ai import chat
    class Client:
        def __getattr__(self, name):
            raise AssertionError('Router accessed paid client')
    def local_chat(**kwargs):
        assert kwargs['provider_id'] == 'ollama'
        seq = json.loads(kwargs['messages'][0]['content'])['message']['seq']
        return {'content': json.dumps({'action':'reply' if seq==2 else 'defer', 'text':'\u70b9\u4e91\u662f\u7a7a\u95f4\u70b9\u7684\u96c6\u5408\u3002' if seq==2 else '', 'question_seqs':[seq]})}
    monkeypatch.setattr(chat, 'chat_with_provider', local_chat)
    sent = []
    svc = SimpleNamespace(store=store, workers=[], last_error=None, last_poll_at=None,
        config={}, stop_event=threading.Event(), poll_once=lambda: None,
        sender=lambda *a, **kw: sent.append(a) or {})
    hook = {**HOOK, 'router_mode': 'flash_semantic'}
    assert process_message_routes(svc, hook, Client())
    assert store.message_routes('group')[1]['lane'] == 'reply'
    assert [m['seq'] for m in store.pending_messages('group')] == [1]
    assert process_message_routes(svc, hook, Client())
    assert sent == [('1@chatroom', '点云是空间点的集合。')]
    assert store.hook('group')['turn_id'] == 'main-turn'
    assert store.hook('group')['status'] == 'running'
    assert process_message_routes(svc, hook, Client())
    assert [m['seq'] for m in store.pending_messages('group')] == [1, 3]


def test_flash_quick_model_failure_retains_original_for_main(tmp_path, monkeypatch):
    store = fixture_store(tmp_path)
    ingest(store, 1, '请解释', ['bot'])
    from backend.core.ai import chat
    class Client:
        def __getattr__(self, name):
            raise AssertionError('Router accessed paid client')
    def fail(**kwargs):
        assert kwargs['provider_id'] == 'ollama'
        raise TimeoutError('local unavailable')
    monkeypatch.setattr(chat, 'chat_with_provider', fail)
    svc = SimpleNamespace(store=store, workers=[], last_error=None, last_poll_at=None)
    assert process_message_routes(svc, {**HOOK, 'router_mode': 'flash_semantic'}, Client())
    assert [m['seq'] for m in store.pending_messages('group')] == [1]
    assert store.get_meta('message_router_fallback:group')['seq'] == 1


def test_routing_worker_never_constructs_execution_client(tmp_path, monkeypatch):
    from backend.core.messaging.wechat_agent import WechatAgentService
    from backend.core.messaging import wechat_message_routing as routing
    service = object.__new__(WechatAgentService)
    service.store = fixture_store(tmp_path)
    service.stop_event = threading.Event()
    service.record_worker_health = lambda *args: None
    service.new_client = lambda *args: (_ for _ in ()).throw(AssertionError('Paid client constructed'))
    def route(svc, hook, client):
        assert client is None
        svc.stop_event.set()
    monkeypatch.setattr(routing, 'process_message_routes', route)
    service._routing_loop(HOOK)
