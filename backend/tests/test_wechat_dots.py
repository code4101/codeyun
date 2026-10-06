import base64
import hashlib
import hmac
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.messaging.wechat_dots import DotsStore, EVENT, process_dots_hook, public_callback
from backend.tests.test_wechat_message_routing import fixture_store, ingest, HOOK


SECRET = 'whsec_'+base64.b64encode(b'k'*32).decode()


def verify_transport(url, body, headers):
    signed = (headers['webhook-id']+'.'+headers['webhook-timestamp']+'.').encode()+body
    expected = base64.b64encode(hmac.new(b'k'*32,signed,hashlib.sha256).digest()).decode()
    assert headers['webhook-signature']=='v1,'+expected
    payload=json.loads(body)
    return 200, {'challenge':payload['challenge']} if payload.get('type')=='verification' else {}


def subscription(store, key='group'):
    return store.subscribe('code4101',key,{'mode':'webhook','url':'https://receiver.example/callback','secret':SECRET},
                           transport=verify_transport)


def service(store, sent):
    return SimpleNamespace(store=store,workers=[],last_error=None,last_poll_at=None,
        config={'reply_max_chars':800},stop_event=threading.Event(),sender=lambda *a,**kw:sent.append(a) or {'result':1})


def test_dots_rollover_uses_shared_handoff_before_admitting_new_day(tmp_path, monkeypatch):
    from backend.core.messaging.wechat_agent import WechatAgentService
    from backend.tests.test_wechat_agent import PlainClient
    dots = DotsStore(tmp_path/'dots.sqlite')
    inbox = fixture_store(tmp_path)
    svc = service(inbox, [])
    svc.prepare_session_handoff = lambda *a, **k: WechatAgentService.prepare_session_handoff(svc, *a, **k)
    subscription(dots)
    monkeypatch.setattr(dots, 'dispatch', lambda *a: False)
    inbox.update_hook('group', session_day='2000-01-01', last_question_at=1, summary='旧背景')
    ingest(inbox, 1, '新一天的任务', ['bot'])
    inbox.record_message_route('group', 1, 'main')
    process_dots_hook(svc, {**HOOK, 'processor':'dots'}, dots=dots, client=PlainClient())
    assert inbox.get_meta('session_handoff:group')['summary'].startswith('压缩交接')
    assert dots.tasks('group')[0]['session_day'] == inbox.get_meta('session_handoff:group')['to_day']


def test_subscription_signature_persistence_and_retry_identity(tmp_path):
    dots=DotsStore(tmp_path/'dots.sqlite')
    first=subscription(dots)
    assert subscription(dots)['id']==first['id']
    with pytest.raises(ValueError,match='already owns'):
        dots.subscribe('code4101','group',{'mode':'webhook','url':'https://other.example/callback','secret':SECRET},
                       transport=verify_transport)
    task=dots.append(HOOK,[{'seq':1}], '2026-10-06')
    seen=[]
    def failure(url,body,headers):
        seen.append((headers['webhook-id'],body))
        return 503,{}
    assert dots.dispatch('group',transport=failure)
    dots=DotsStore(dots.path)
    with dots.transaction() as db:
        db.execute('UPDATE events SET retry_at=0')
    assert dots.dispatch('group',transport=failure)
    assert seen[0]==seen[1]
    assert dots.task(task)['revision']==1
    assert dots.status()['events'][0]['attempts']==2
    with pytest.raises(ValueError):
        dots.subscribe('owner','group',{'mode':'webhook','url':'https://example.com','secret':'bad'},transport=verify_transport)


def test_connection_diagnostics_distinguishes_missing_cloud_setup(tmp_path):
    dots=DotsStore(tmp_path/'dots.sqlite')
    assert dots.status()['connection']['stage']=='awaiting_plugin_connection'
    dots.put_credential('client','client_test',{'redirect_uris':['https://chatgpt.com/callback']},60)
    assert dots.status()['connection']['stage']=='awaiting_oauth_authorization'
    dots.put_credential('refresh','refresh_hash',{'scope':'wechat:future-community'},60)
    assert dots.status()['connection']['stage']=='awaiting_dots_subscription'
    dots.record_connection_check('events_subscription','callback_failed')
    assert dots.status()['connection']['checks'][0]['result']=='callback_failed'
    subscription(dots)
    assert dots.status()['connection']['stage']=='subscribed'
    with pytest.raises(ValueError):
        dots.record_connection_check('events_subscription',SECRET)


def test_images_wait_and_revision_rejects_old_reply_then_engineering_sends(tmp_path,monkeypatch):
    dots=DotsStore(tmp_path/'dots.sqlite')
    inbox=fixture_store(tmp_path)
    sent=[]
    svc=service(inbox,sent)
    hook={**HOOK,'processor':'dots','followup_seconds':300}
    assert process_dots_hook(svc,hook,dots=dots) is None
    subscription(dots)
    monkeypatch.setattr(dots,'dispatch',lambda *a:False)
    inbox.ingest('bot',{'events':[{'message_id':'photo','chat_id':'1@chatroom','sender_id':'player',
        'timestamp':1,'text':'<img/>','message_type':3}], 'cursor':{}})
    inbox.record_message_route('group',1,'main')
    assert not process_dots_hook(svc,hook,dots=dots)
    assert dots.tasks('group')==[]
    ingest(inbox,2,'请解释图片',['bot'])
    inbox.record_message_route('group',2,'main')
    process_dots_hook(svc,hook,dots=dots)
    task=dots.tasks('group')[0]
    assert task['seqs']==[1,2]
    dots.submit(task['id'],1,[1,2],'解释完成',available=[1,2])
    ingest(inbox,3,'纠正：只看右边')
    inbox.record_message_route('group',3,'main')
    process_dots_hook(svc,hook,dots=dots)
    assert not sent
    assert dots.task(task['id'])['revision']==2
    with pytest.raises(ValueError,match='Stale'):
        dots.submit(task['id'],1,[2],'旧答案',available=[1,2,3])
    text='新的解释'*300
    dots.submit(task['id'],2,[1,2,3],text,available=[1,2,3])
    assert process_dots_hook(svc,hook,dots=dots)
    assert len(sent)>=2 and all(len(a[1])<=800 for a in sent)
    assert inbox.pending_messages('group')==[]
    assert dots.task(task['id'])['status']=='sent'
    process_dots_hook(svc,hook,dots=dots)
    assert len(sent)==2


def test_expired_subscription_does_not_redispatch_to_codex_and_partial_reply_keeps_work(tmp_path,monkeypatch):
    dots=DotsStore(tmp_path/'dots.sqlite'); subscription(dots)
    inbox=fixture_store(tmp_path); sent=[]; svc=service(inbox,sent)
    hook={**HOOK,'processor':'dots'}
    for n in (1,2):
        ingest(inbox,n,'请回答',['bot']); inbox.record_message_route('group',n,'main')
    monkeypatch.setattr(dots,'dispatch',lambda *a:False)
    process_dots_hook(svc,hook,dots=dots)
    task=dots.tasks('group')[0]
    with dots.transaction() as db:
        db.execute('UPDATE subscriptions SET expires=0')
    assert process_dots_hook(svc,hook,dots=dots) is False
    dots.submit(task['id'],1,[1],'第一个答案',available=[1,2])
    assert process_dots_hook(svc,hook,dots=dots)
    assert [m['seq'] for m in inbox.pending_messages('group')]==[2]
    # No subscriber may mean fallback for new work, but the second question in
    # this already assigned batch must remain Dots-owned.
    assert process_dots_hook(svc,hook,dots=dots) is False


def test_callback_blocks_local_mixed_dns_and_redirects(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError): public_callback('https://example.com/callback')
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',(ip,443)) for ip in ('8.8.8.8','10.0.0.1')])
    with pytest.raises(ValueError): public_callback('https://example.com/callback')
    for url in ('http://example.com','https://a:b@example.com','https://example.com:444/'):
        with pytest.raises(ValueError): public_callback(url)


def test_photo_after_text_is_still_waiting_and_crash_after_partial_send_recovers(tmp_path,monkeypatch):
    dots=DotsStore(tmp_path/'dots.sqlite'); subscription(dots)
    inbox=fixture_store(tmp_path); sent=[]; svc=service(inbox,sent)
    hook={**HOOK,'processor':'dots'}
    ingest(inbox,1,'请查询',['bot']); inbox.record_message_route('group',1,'main')
    inbox.ingest('bot',{'events':[{'message_id':'late-photo','chat_id':'1@chatroom','sender_id':'player',
        'timestamp':2,'text':'<img/>','message_type':3}], 'cursor':{}})
    inbox.record_message_route('group',2,'main')
    monkeypatch.setattr(dots,'dispatch',lambda *a:False)
    process_dots_hook(svc,hook,dots=dots)
    task=dots.tasks('group')[0]
    assert task['seqs']==[1]
    dots.submit(task['id'],1,[1],'查询结果',available=[1])
    # Persisted queued draft survives a crash between the two store commits.
    original=dots.finish
    monkeypatch.setattr(dots,'finish',lambda *a:(_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError,match='crash'):
        process_dots_hook(svc,hook,dots=dots)
    assert len(sent)==1
    monkeypatch.setattr(dots,'finish',original)
    assert process_dots_hook(svc,hook,dots=dots)
    assert len(sent)==1
    assert [m['seq'] for m in inbox.pending_messages('group')]==[2]
    assert dots.task(task['id'])['status']=='sent'


def test_oauth_pkce_scope_and_rpc_requires_credentials(tmp_path,monkeypatch):
    import backend.api.wechat_dots as api
    dots=DotsStore(tmp_path/'dots.sqlite')
    monkeypatch.setattr(api,'bridge_store',lambda:dots)
    monkeypatch.setattr(api,'enabled_hooks',lambda:{'future-community':{**HOOK,'key':'future-community'}})
    app=FastAPI(); app.include_router(api.router); client=TestClient(app)
    assert client.post(api.PREFIX+'/mcp',json={'jsonrpc':'2.0','id':1,'method':'server/discover'}).status_code==401
    assert client.get('/.well-known/oauth-protected-resource/api/wechat-dots/mcp').json()['resource']==api.MCP_URL
    registration=client.post(api.PREFIX+'/oauth/register',json={'redirect_uris':['https://chatgpt.com/callback']}).json()
    identity=registration['client_id']
    verifier='v'*64
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    grant={'client_id':identity,'redirect_uri':'https://chatgpt.com/callback','challenge':challenge,
           'scope':api.SCOPE,'resource':api.MCP_URL,'owner':'code4101'}
    dots.put_credential('code',api.digest('good'),grant,120)
    data={'grant_type':'authorization_code','client_id':identity,'code':'good',
          'redirect_uri':grant['redirect_uri'],'code_verifier':'x'*64}
    assert client.post(api.PREFIX+'/oauth/token',data=data).status_code==400
    data['code_verifier']=verifier
    response=client.post(api.PREFIX+'/oauth/token',data=data)
    assert response.status_code==200
    assert client.post(api.PREFIX+'/oauth/token',data=data).status_code==400
    headers={'Authorization':'Bearer '+response.json()['access_token']}
    result=client.post(api.PREFIX+'/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'server/discover'}).json()
    assert result['result']['capabilities']['events']=={}
    assert client.post(api.PREFIX+'/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call',
        'params':{'name':'read_wechat_task','arguments':{'task_id':'foreign'}}}).json()['error']['code']==-32602


def test_image_tool_requires_later_text_and_uses_readable_high_resource(tmp_path,monkeypatch):
    import sys
    import backend.api.wechat_dots as api
    import backend.core.messaging.wechat_agent as agent
    from PIL import Image
    inbox=fixture_store(tmp_path)
    inbox.ingest('bot',{'events':[{'message_id':'photo','chat_id':'1@chatroom','sender_id':'player',
        'timestamp':1,'text':'<img/>','local_id':123,'message_type':3}], 'cursor':{}})
    inbox.record_message_route('group',1,'main')
    dots=DotsStore(tmp_path/'dots.sqlite')
    task=dots.append(HOOK,[{'seq':1}],'2026-10-06')
    monkeypatch.setattr(api,'enabled_hooks',lambda:{'group':HOOK})
    monkeypatch.setattr(agent,'agent_root',lambda:tmp_path)
    with pytest.raises(ValueError,match='awaits'):
        api.call_tool(dots,'read_wechat_image',{'task_id':task,'seq':1})
    ingest(inbox,2,'请分析这张照片'); inbox.record_message_route('group',2,'main')
    dots.append(HOOK,[{'seq':1},{'seq':2}],'2026-10-06')
    image=tmp_path/'high.png'; Image.new('RGB',(4,4),'blue').save(image)
    def resources(chat,local):
        assert chat=='1@chatroom' and local==123
        return {'items':[{'export':{'readable':False,'kind':'image','variant':'high','stored_path':'unreadable'}},
            {'export':{'readable':True,'kind':'image','variant':'thumbnail','stored_path':'wrong-low'}},
            {'export':{'readable':True,'kind':'image','variant':'high','stored_path':str(image)}}]}
    monkeypatch.setitem(sys.modules,'pyxllib.autogui.wechat_accounts',
        SimpleNamespace(get_account_storage=lambda account:SimpleNamespace(message_resources=resources)))
    result=api.call_tool(dots,'read_wechat_image',{'task_id':task,'seq':1})
    assert base64.b64decode(result['_image_content']['data'])==image.read_bytes()
    assert result['_image_content']['mimeType']=='image/png'
