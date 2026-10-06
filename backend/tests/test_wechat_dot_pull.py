import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.messaging.wechat_agent_store import AgentStore
from backend.core.messaging.wechat_dot_pull import WechatDotPull, PullError, HOOK_KEY, ACCOUNT_ID, CHAT_ID
from scripts.wechat_dot_wait import wait_for_messages, CodeYunClient


@pytest.fixture
def setup(tmp_path):
    store = AgentStore(tmp_path/'events.sqlite')
    store.ensure_hook(HOOK_KEY, ACCOUNT_ID, CHAT_ID)
    hook = {'key':HOOK_KEY, 'account_id':ACCOUNT_ID, 'chat_id':CHAT_ID, 'processor':'dot_pull', 'message_routing':False}
    health = lambda: {'running':True, 'listener':{'last_poll_at':time.time()}}
    store.set_meta('service', {'pid':123, 'heartbeat':time.time(), 'stopped':False})
    store.set_meta('processor_owner:'+HOOK_KEY, {'pid':123, 'processor':'dot_pull'})
    sent = []
    service = WechatDotPull(store=store, config={'hooks':[hook]}, health=health,
                           sender=lambda chat,text,**kw: sent.append((chat,text,kw)) or {'accepted':1}, refresh=lambda:None)
    return store, service, sent


def ingest(store, n, *, sender='human', chat=CHAT_ID):
    event = {'message_id':f'{chat}:{n}', 'chat_id':chat, 'sender_id':sender,
             'timestamp':time.time(), 'text':f'问题{n}', 'message_type':1, 'local_id':n}
    store.ingest(ACCOUNT_ID, {'events':[event], 'cursor':{'n':n}})


def test_read_never_acknowledges_all_lanes_and_replays_stable_input(setup):
    store, service, sent = setup
    ingest(store, 1); ingest(store, 1)
    ingest(store, 2, sender=ACCOUNT_ID)
    ingest(store, 3, chat='another@chatroom')
    store.enable_message_routing(HOOK_KEY)
    first = service.messages()
    assert first['status'] == 'new_messages'
    assert [m['local_id'] for m in first['messages']] == [1]
    assert service.messages() == first
    assert store.hook(HOOK_KEY)['consumed_seq'] == 0
    assert not sent
    assert service.messages(cursor=first['cursor'])['status'] == 'idle'
    assert service.messages()['status'] == 'new_messages'


def test_explicit_no_reply_ack_is_idempotent_and_not_a_send(setup):
    store, service, sent = setup
    ingest(store, 1)
    seq = service.messages()['messages'][0]['seq']
    cursor = service.messages()['cursor']
    assert service.acknowledge([seq],cursor)['status'] == 'acknowledged'
    ingest(store, 2)
    assert service.acknowledge([seq],cursor)['status'] == 'acknowledged'
    assert service.messages()['messages'][0]['local_id'] == 2
    assert not sent


def test_reply_identity_survives_restart_and_verifies_only_real_self_echo(setup):
    store, service, sent = setup
    ingest(store, 1)
    page = service.messages(); seq = page['messages'][0]['seq']
    result = service.reply('stable-1',[seq],page['cursor'],'完成')
    assert result['status'] == 'sent' and result['verification']['status'] == 'unverified'
    assert len(sent) == 1 and sent[0][0] == CHAT_ID and sent[0][2]['sender_account_id'] == ACCOUNT_ID
    service.store = AgentStore(store.path)
    assert service.reply('stable-1',[seq],page['cursor'],'完成')['reply_id'] == result['reply_id']
    assert len(sent) == 1
    with pytest.raises(PullError,match='request_conflict'):
        service.reply('stable-1',[seq],page['cursor'],'换一个答案')
    service.storage = SimpleNamespace(list_messages=lambda *a,**k: {'items':[{
        'sender_username':ACCOUNT_ID, 'create_time':int(time.time()), 'message_text':sent[0][1], 'server_id':1234}]})
    assert service.receipt('stable-1',verify=True)['verification'] == {'status':'verified_sender_archive','server_ids':['1234']}
    assert service.messages()['status'] == 'idle'


def test_stale_reply_and_foreign_or_resolved_sources_are_rejected(setup):
    store, service, sent = setup
    ingest(store, 1)
    page = service.messages(); seq = page['messages'][0]['seq']
    ingest(store, 2)
    with pytest.raises(PullError,match='stale_context'):
        service.reply('stale',[seq],page['cursor'],'旧答案')
    assert not sent and store.dot_reply_receipt(HOOK_KEY,'stale') is None
    with pytest.raises(PullError):
        service.acknowledge([999],service.messages()['cursor'])


def test_uncertain_send_is_never_reissued_and_blocks_other_decisions(setup):
    store, service, sent = setup
    ingest(store, 1); page=service.messages(); seq=page['messages'][0]['seq']
    def broken(*a,**k):
        sent.append(a)
        raise RuntimeError('native result unknown')
    service.sender = broken
    assert service.reply('uncertain',[seq],page['cursor'],'答案')['status'] == 'uncertain'
    assert service.reply('uncertain',[seq],page['cursor'],'答案')['status'] == 'uncertain'
    assert len(sent) == 1
    with pytest.raises(PullError,match='pull_owner_busy'):
        service.acknowledge([seq],page['cursor'])
    assert service.messages()['status'] == 'new_messages'


@pytest.mark.parametrize('change', ['config', 'pid', 'processor', 'stopped', 'running'])
def test_old_writer_and_unfinished_handoff_fail_closed(setup,change):
    store, service, sent = setup
    ingest(store,1); page=service.messages(); seq=page['messages'][0]['seq']
    if change == 'config': service.hook['processor']='dots'
    elif change == 'pid': store.set_meta('processor_owner:'+HOOK_KEY,{'pid':456,'processor':'dot_pull'})
    elif change == 'processor': store.set_meta('processor_owner:'+HOOK_KEY,{'pid':123,'processor':'dots'})
    elif change == 'stopped': store.set_meta('service',{'pid':123,'heartbeat':time.time(),'stopped':True})
    else: store.update_hook(HOOK_KEY,status='running')
    with pytest.raises(PullError): service.reply('blocked',[seq],page['cursor'],'答案')
    assert not sent and store.dot_reply_receipt(HOOK_KEY,'blocked') is None


def test_sending_crash_receipt_survives_recovery_without_ack(setup):
    store, service, sent = setup
    ingest(store,1); page=service.messages(); seq=page['messages'][0]['seq']
    body=f'CODECLAW_REPLY_TO: {seq}\n答案'
    store.reserve_dot_reply(HOOK_KEY,'crash',[seq],seq,body)
    store.recover()
    assert service.reply('crash',[seq],page['cursor'],'答案')['status']=='uncertain'
    assert not sent and store.hook(HOOK_KEY)['consumed_seq']==0


def test_wait_five_seconds_timeout_resume_cancel_and_errors():
    ticks=[0]; reads=[]
    def clock(): return ticks[0]
    def pause(seconds): ticks[0]+=seconds
    def read(cursor):
        reads.append((ticks[0],cursor)); return {'status':'idle','cursor':'future-community:0'}
    result=wait_for_messages(read,timeout_seconds=10,clock=clock,pause=pause)
    assert result=={'status':'timeout','cursor':'future-community:0'}
    assert [t for t,c in reads]==[0,5,10]
    new={'status':'new_messages','messages':[{'seq':1}],'cursor':'future-community:1'}
    assert wait_for_messages(lambda c:new,cursor=result['cursor'])==new
    cancel=threading.Event(); cancel.set()
    assert wait_for_messages(lambda c:pytest.fail('cancelled before read'),cancel=cancel)['status']=='cancelled'
    for status in ['error','disconnected']:
        error={'status':status,'error':{'code':'bounded','retryable':status=='disconnected'}}
        assert wait_for_messages(lambda c:error,cursor=result['cursor'])['cursor']==result['cursor']


def test_wait_does_not_ack_when_cancelled_during_read():
    cancel=threading.Event()
    def read(cursor):
        cancel.set(); return {'status':'new_messages','messages':[{'seq':1}],'cursor':'future-community:1'}
    assert wait_for_messages(read,cursor='future-community:0',cancel=cancel)=={'status':'cancelled','cursor':'future-community:0'}


@pytest.mark.parametrize('code,expected',[(401,'authentication_required'),(403,'access_denied'),(404,'endpoint_unavailable')])
def test_cli_preserves_http_auth_boundary(code,expected):
    response=SimpleNamespace(status_code=code)
    client=CodeYunClient(session=SimpleNamespace(request=lambda *a,**k:response),token='existing')
    result=client.call('GET','/messages')
    assert result['error']=={'code':expected,'retryable':False}


def test_cli_connection_loss_keeps_reply_request_identity():
    def fail(*a,**k): raise requests.Timeout()
    result=CodeYunClient(session=SimpleNamespace(request=fail),token='').call('POST','/replies',payload={'request_id':'same'})
    assert result['status']=='disconnected' and result['request_id']=='same'
    with pytest.raises(ValueError): CodeYunClient(base_url='https://other.example',token='')


def test_http_routes_require_auth_and_use_public_business_service(setup,monkeypatch):
    import backend.api.wechat_archive as api
    import backend.core.messaging.wechat_dot_pull as module
    store,service,sent=setup
    monkeypatch.setattr(module,'WechatDotPull',lambda:service)
    app=FastAPI(); app.include_router(api.router,prefix='/api/wechat-archive')
    app.dependency_overrides[api.require_wechat_archive_access]=lambda:None
    client=TestClient(app); ingest(store,1)
    r=client.get('/api/wechat-archive/agent/dot/messages')
    assert r.status_code==200 and r.json()['status']=='new_messages'
    service.hook['processor']='dots'
    r=client.post('/api/wechat-archive/agent/dot/replies',json={
        'request_id':'blocked','source_seqs':[1],'cursor':r.json()['cursor'],'text':'答案'})
    assert r.status_code==409 and r.json()['error']['code']=='ownership_not_transferred'
    assert not sent


def test_pull_processor_has_no_model_or_dot_execution_and_other_hooks_unchanged(setup):
    from backend.core.messaging.wechat_agent import WechatAgentService
    store,service,sent=setup
    fake=SimpleNamespace()
    assert WechatAgentService.process_hook(fake,service.hook,object()) is False
    store.subscribe_codex_thread('thread',HOOK_KEY,[])
    store.enqueue_codex_completion('thread','turn','mirrored answer')
    notification=store.pending_codex_notifications()[0]
    assert store.reserve_codex_notification(notification['id']) is None


def test_backlog_restores_unsolved_without_replaying_sent_or_claiming_active(setup):
    store, service, sent = setup
    ingest(store,1)
    old=store.reserve_reply(HOOK_KEY,1,'旧处理器已经回复')
    store.finish_reply(old,'sent',{'accepted':1})
    ingest(store,2); ingest(store,3)
    store.update_hook(HOOK_KEY,status='running',turn_id='old-turn',thread_id='old-thread')
    store.set_meta('context_delivery:'+HOOK_KEY,{'submitted_seq':2})
    result=service.backlog()
    assert [m['seq'] for m in result['sent']]==[1]
    assert [m['seq'] for m in result['in_flight']]==[2]
    assert [m['seq'] for m in result['unresolved']]==[3]
    assert not result['reply_permitted']
    assert result['sent'][0]['receipt']['id']==old
    assert not result['sent'][0]['receipt']['delivery_verified']
    assert store.hook(HOOK_KEY)['consumed_seq']==1 and not sent
    with pytest.raises(PullError,match='pull_owner_busy'):
        service.reply('steal',[2],service.messages()['cursor'],'抢答')
    store.update_hook(HOOK_KEY,status='idle',turn_id=None)
    assert [m['seq'] for m in service.messages()['messages']]==[2,3]


def test_backlog_pages_and_acknowledged_input_is_not_claimed_as_sent(setup):
    store,service,sent=setup
    ingest(store,1); ingest(store,2)
    service.acknowledge([1],service.messages()['cursor'])
    first=service.backlog(limit=1)
    assert first['has_more'] and first['cursor']=='future-community:1'
    assert first['resolved_without_reply'][0]['seq']==1 and not first['sent']
    assert service.backlog(cursor=first['cursor'])['unresolved'][0]['seq']==2


def test_backlog_uncertain_is_in_flight_and_never_replayed(setup):
    store,service,sent=setup
    ingest(store,1)
    row=store.reserve_reply(HOOK_KEY,1,'旧发送结果未知')
    store.finish_reply(row,'uncertain',{'error':'unknown'})
    result=service.backlog()
    assert result['in_flight'][0]['reason']=='uncertain'
    assert not result['unresolved'] and not result['reply_permitted']


def test_unbounded_wait_stays_in_one_program_until_external_cancel():
    cancel=threading.Event(); ticks=[]
    def read(cursor): return {'status':'idle','cursor':'future-community:0'}
    def pause(seconds):
        ticks.append(seconds)
        if len(ticks)==20: cancel.set()
    result=wait_for_messages(read,timeout_seconds=None,cancel=cancel,pause=pause)
    assert result['status']=='cancelled' and ticks==[5]*20


def test_concurrent_same_request_has_one_native_send(setup):
    from concurrent.futures import ThreadPoolExecutor
    store,service,sent=setup
    ingest(store,1); cursor=service.messages()['cursor']
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:service.reply('same',[1],cursor,'同一答复'),range(2)))
    assert len(sent)==1 and len({r['reply_id'] for r in results})==1
    assert all(r['status']=='sent' for r in results)


def test_concurrent_ack_and_send_never_send_after_resolution(setup):
    from concurrent.futures import ThreadPoolExecutor
    store,service,sent=setup
    ingest(store,1); cursor=service.messages()['cursor']
    def operation(reply):
        try:
            return service.reply('one',[1],cursor,'答案') if reply else service.acknowledge([1],cursor)
        except (PullError,ValueError) as exc: return str(exc)
    with ThreadPoolExecutor(2) as pool: list(pool.map(operation,[True,False]))
    assert len(sent)<=1 and store.hook(HOOK_KEY)['consumed_seq']==1
    if sent: assert store.dot_reply_receipt(HOOK_KEY,'one')['status']=='sent'


def test_reply_refresh_sees_correction_before_outbox_reservation(setup):
    store,service,sent=setup
    ingest(store,1); cursor=service.messages()['cursor']
    service.refresh=lambda:ingest(store,2)
    with pytest.raises(PullError,match='stale_context'):
        service.reply('too-old',[1],cursor,'过时答案')
    assert not sent and store.dot_reply_receipt(HOOK_KEY,'too-old') is None


def test_account_poll_cursor_is_serialized_across_two_callers(setup):
    from concurrent.futures import ThreadPoolExecutor
    from backend.core.messaging.wechat_agent import refresh_account_events
    store,service,sent=setup
    seen=[]
    class Source:
        def poll_updates(self,cursor):
            seen.append(cursor)
            time.sleep(.02)
            return {'events':[],'cursor':{'generation':len(seen)},'has_more':False}
    source=Source()
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(lambda _:refresh_account_events(store,ACCOUNT_ID,source),range(2)))==[0,0]
    assert seen==[None,{'generation':1}]
    assert store.get_meta('cursor:'+ACCOUNT_ID)=={'generation':2}


def test_normal_account_login_uses_public_route_and_memory_only_session():
    calls=[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        return SimpleNamespace(status_code=200,is_redirect=False,json=lambda:{'access_token':'temporary-user-session','token_type':'bearer'})
    client=CodeYunClient(session=SimpleNamespace(request=request),token='')
    result=client.login('code4101','test-password')
    assert result=={'status':'authenticated'}
    assert calls[0][0]=='POST' and calls[0][1]=='http://127.0.0.1:8000/api/auth/login/json'
    assert calls[0][2]['json']=={'username':'code4101','password':'test-password'}
    assert not calls[0][2]['allow_redirects']
    assert client.headers=={'Authorization':'Bearer temporary-user-session'}
    assert 'access_token' not in json.dumps(result) and 'test-password' not in json.dumps(result)


def test_login_rejection_and_connection_loss_never_forge_identity():
    client=CodeYunClient(session=SimpleNamespace(request=lambda *a,**k:SimpleNamespace(status_code=401,is_redirect=False)),token='')
    assert client.login('code4101','test-password')['error']['code']=='login_rejected'
    assert not client.headers
    def disconnected(*a,**k): raise requests.ConnectionError()
    client.session=SimpleNamespace(request=disconnected)
    assert client.login('code4101','test-password')['status']=='disconnected'
    assert not client.headers
