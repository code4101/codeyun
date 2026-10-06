"""Private, OAuth/PKCE-scoped MCP endpoint for the owner's Dots group robot.

Only configured processor=dots groups are visible. No general filesystem,
shell, archive, diary or game tools are exposed through this connector.
OAuth uses the existing CodeYun login contract and issues separate bounded
opaque credentials; plugin access never inherits the owner's general token.
"""
from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jsonschema import validate, ValidationError

from backend.core.messaging.wechat_dots import (
    EVENT, INSTRUCTIONS, CallbackError, bridge_store, canonical, digest,
)

router = APIRouter()
BASE = 'https://code4101.com'
PREFIX = '/api/wechat-dots'
MCP_URL = BASE+PREFIX+'/mcp'
ISSUER = BASE+PREFIX+'/oauth'
SCOPE = 'wechat:future-community'


def enabled_hooks():
    from backend.core.messaging.wechat_agent import load_config
    # Explicit configured scope, never all groups belonging to this owner.
    return {h['key']:h for h in load_config().get('hooks',[]) if h.get('processor')=='dots'
            and h['key']=='future-community' and h.get('kind','group')=='group'}


def authorize_token(request):
    token = request.headers.get('authorization','').removeprefix('Bearer ')
    identity = bridge_store().credential('access', digest(token)) if token else None
    if not identity or identity.get('scope')!=SCOPE or identity.get('owner')!='code4101':
        raise HTTPException(401, 'Dots connection required', headers={'WWW-Authenticate':
            'Bearer resource_metadata="'+BASE+'/.well-known/oauth-protected-resource/api/wechat-dots/mcp"'})
    return identity['owner']


@router.get('/.well-known/oauth-protected-resource/api/wechat-dots/mcp')
def resource_metadata():
    return {'resource':MCP_URL, 'authorization_servers':[ISSUER], 'scopes_supported':[SCOPE],
            'bearer_methods_supported':['header'], 'resource_name':'CodeYun 未来社机器人'}


@router.get('/.well-known/oauth-authorization-server/api/wechat-dots/oauth')
def oauth_metadata():
    return {'issuer':ISSUER, 'authorization_endpoint':ISSUER+'/authorize', 'token_endpoint':ISSUER+'/token',
            'registration_endpoint':ISSUER+'/register', 'response_types_supported':['code'],
            'grant_types_supported':['authorization_code','refresh_token'], 'scopes_supported':[SCOPE],
            'token_endpoint_auth_methods_supported':['none'], 'code_challenge_methods_supported':['S256']}


@router.post(PREFIX+'/oauth/register')
async def register_client(request: Request):
    data = await request.json()
    redirects = data.get('redirect_uris', [])
    if not isinstance(redirects,list) or not 1<=len(redirects)<=5:
        raise HTTPException(400,'Invalid redirect_uris')
    for uri in redirects:
        p = urlsplit(uri)
        if p.fragment or p.username or p.password or not p.hostname or not (
                p.scheme=='https' or p.scheme=='http' and p.hostname in ('127.0.0.1','localhost','::1')):
            raise HTTPException(400,'HTTPS or loopback redirect required')
    if data.get('token_endpoint_auth_method','none')!='none':
        raise HTTPException(400,'Only public PKCE clients supported')
    identity = 'client_'+secrets.token_hex(24)
    result = {'client_id':identity,'redirect_uris':redirects,'token_endpoint_auth_method':'none',
              'grant_types':['authorization_code','refresh_token'],'response_types':['code']}
    bridge_store().put_credential('client',identity,result,365*86400)
    return JSONResponse(result,status_code=201)


@router.get(PREFIX+'/oauth/authorize', response_class=HTMLResponse)
def login_form(client_id: str, redirect_uri: str, code_challenge: str,
               response_type: str='code', code_challenge_method: str='S256',
               scope: str=SCOPE, state: str='', resource: str=MCP_URL):
    store = bridge_store()
    client = store.credential('client',client_id)
    if not client or redirect_uri not in client['redirect_uris'] or response_type!='code' or code_challenge_method!='S256' or scope!=SCOPE or resource!=MCP_URL or not re.fullmatch(r'[A-Za-z0-9_-]{43}',code_challenge):
        raise HTTPException(400,'Invalid authorization request')
    nonce = secrets.token_urlsafe(32)
    store.put_credential('consent',digest(nonce),{'client_id':client_id,'redirect_uri':redirect_uri,
        'challenge':code_challenge,'state':state,'scope':scope,'resource':resource},600)
    return HTMLResponse('<!doctype html><html lang="zh"><meta charset="utf-8"><title>连接未来社 Dots</title>'
        '<body><h1>连接 CodeYun 未来社机器人</h1><p>允许读取未来社机器人任务，并提交由工程发送的群回复。'
        '不开放其他群、私聊、日记、财务或游戏账号。</p><form method="post">'
        '<input type="hidden" name="nonce" value="'+html.escape(nonce)+'">'
        '<label>CodeYun 所有者账号 <input name="username" autocomplete="username" required></label><br>'
        '<label>密码 <input name="password" type="password" autocomplete="current-password" required></label><br>'
        '<button type="submit">登录并连接</button></form></body></html>',
        headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; form-action 'self'; frame-ancestors 'none'"})


@router.post(PREFIX+'/oauth/authorize')
async def consent(request: Request):
    if request.headers.get('origin') not in (None,BASE):
        raise HTTPException(403,'Foreign origin')
    form = await request.form()
    store = bridge_store()
    data = store.credential('consent',digest(str(form.get('nonce',''))),consume=True)
    if not data:
        raise HTTPException(400,'Expired authorization form')
    from backend.api.auth import login_json
    from backend.schemas import UserLogin
    from backend.db import engine
    from sqlmodel import Session
    if form.get('username')!='code4101':
        raise HTTPException(403,'Only project owner may connect')
    with Session(engine) as session:
        try:
            login_json(UserLogin(username=str(form.get('username')),password=str(form.get('password'))),session=session)
        except HTTPException:
            store.record_connection_check('oauth_authorization','login_failed')
            raise
    code = secrets.token_urlsafe(32)
    store.put_credential('code',digest(code),{**data,'owner':'code4101'},120)
    store.record_connection_check('oauth_authorization','success')
    separator = '&' if urlsplit(data['redirect_uri']).query else '?'
    return RedirectResponse(data['redirect_uri']+separator+urlencode({'code':code,'state':data['state']}),
                            status_code=303,headers={'Cache-Control':'no-store'})


@router.post(PREFIX+'/oauth/token')
async def exchange_token(request: Request):
    data = dict(await request.form())
    store = bridge_store()
    kind = 'code' if data.get('grant_type')=='authorization_code' else 'refresh' if data.get('grant_type')=='refresh_token' else None
    if not kind:
        raise HTTPException(400,'Unsupported grant')
    raw = data.get('code' if kind=='code' else 'refresh_token','')
    grant = store.credential(kind,digest(raw))
    if not grant or grant['client_id']!=data.get('client_id') or data.get('resource',MCP_URL)!=MCP_URL:
        raise HTTPException(400,'Invalid grant')
    if kind=='code':
        verifier = data.get('code_verifier','')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        if not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}',verifier) or not hmac.compare_digest(challenge,grant['challenge']) or grant['redirect_uri']!=data.get('redirect_uri'):
            raise HTTPException(400,'Invalid PKCE or redirect')
    if not store.credential(kind,digest(raw),consume=True):
        raise HTTPException(400,'Grant already used')
    access, refresh = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    identity = {k:grant[k] for k in ('client_id','owner','scope','resource')}
    store.put_credential('access',digest(access),identity,3600)
    store.put_credential('refresh',digest(refresh),identity,30*86400)
    store.record_connection_check('oauth_token','success')
    return JSONResponse({'access_token':access,'refresh_token':refresh,'token_type':'Bearer','expires_in':3600,'scope':SCOPE},
                        headers={'Cache-Control':'no-store'})


def object_schema(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


TOOLS = [
    {'name':'list_wechat_tasks','title':'查看未来社任务','description':'读取已交给Dots的未来社任务及状态。',
     'inputSchema':object_schema({'hook_key':{'type':'string','enum':['future-community']}}),
     'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}},
    {'name':'read_wechat_task','title':'读取任务和最新上下文','description':'处理或答复前读取最新revision、原消息及群内上下文。图片必须等后续人工文字说明。',
     'inputSchema':object_schema({'task_id':{'type':'string'}},['task_id']),
     'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}},
    {'name':'read_wechat_image','title':'读取已解释的任务图片','description':'仅在图片之后已有人工文字说明时读取任务图片；只读取已下载的真实资源，优先高清。',
     'inputSchema':object_schema({'task_id':{'type':'string'},'seq':{'type':'integer'}},['task_id','seq']),
     'annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False}},
    {'name':'submit_wechat_reply','title':'提交群回复','description':'提交已处理seq的答复，工程检查revision后发送；queued仅为排队，读取任务核验送达。',
     'inputSchema':object_schema({'task_id':{'type':'string'},'revision':{'type':'integer'},
         'question_seqs':{'type':'array','items':{'type':'integer'},'minItems':1,'uniqueItems':True},
         'text':{'type':'string','minLength':1,'maxLength':16000}},['task_id','revision','question_seqs','text']),
     'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True,'openWorldHint':True}},
]
EVENT_SCHEMA = {'name':EVENT,'description':'未来社群明确@或相关补充形成的机器人任务发生变化；先读取任务再处理。',
    'delivery':['webhook'], 'inputSchema':object_schema({'hook_key':{'type':'string','enum':['future-community']}},['hook_key']),
    'payloadSchema':object_schema({'task_id':{'type':'string'},'hook_key':{'type':'string'},'revision':{'type':'integer'}},
                                  ['task_id','hook_key','revision'])}


def scoped_task(store, task_id):
    task = store.task(task_id)
    if task['hook_key'] not in enabled_hooks():
        raise ValueError('Task outside connected scope')
    return task


def call_tool(store, name, arguments):
    definition = next((t for t in TOOLS if t['name']==name), None)
    if not definition:
        raise ValueError('Unknown tool')
    validate(arguments, definition['inputSchema'])
    hooks = enabled_hooks()
    if name=='list_wechat_tasks':
        key = arguments.get('hook_key','future-community')
        if key not in hooks:
            raise ValueError('Group is not configured for Dots')
        return {'tasks':[{k:t[k] for k in ('id','hook_key','revision','status','session_day','seqs')} for t in store.tasks(key)]}
    task = scoped_task(store,arguments['task_id'])
    from backend.core.messaging.wechat_agent import agent_root, model_message
    from backend.core.messaging.wechat_agent_store import AgentStore
    inbox = AgentStore(agent_root()/'events.sqlite')
    pending = inbox.pending_messages(task['hook_key'])
    messages = [m for m in pending if m['seq'] in task['seqs']]
    if name=='read_wechat_task':
        through = max(task['seqs'])
        return {'task':{k:task[k] for k in ('id','hook_key','revision','status','session_day','seqs')},
            'scope':hooks[task['hook_key']]['description'], 'messages':[model_message(m) for m in messages],
            'recent_context':[model_message(m) for m in inbox.recent(task['hook_key'],through,limit=20)],
            'delivery_receipts':inbox.deliveries(task['hook_key']), 'instructions':INSTRUCTIONS}
    from backend.core.messaging.wechat_content import is_image_message
    if name=='read_wechat_image':
        message=next((m for m in messages if m['seq']==arguments['seq'] and is_image_message(m)),None)
        if not message or not any(m['seq']>message['seq'] and not is_image_message(m)
                                  and str(m.get('text') or '').strip() for m in messages):
            raise ValueError('Image is outside task or awaits later human explanation')
        from pyxllib.autogui.wechat_accounts import get_account_storage
        from pathlib import Path
        from PIL import Image
        hook=hooks[task['hook_key']]
        resources=get_account_storage(hook['account_id']).message_resources(hook['chat_id'],message['local_id'])
        exports=[item['export'] for item in resources.get('items',[]) if item.get('export',{}).get('readable')
                 and item['export'].get('kind')=='image']
        exports.sort(key=lambda e:e.get('variant')!='high')
        if not exports:
            return {'status':'unreadable','seq':message['seq'],'detail':'图片尚未下载或解码，未读取实际内容。'}
        path=Path(exports[0]['stored_path'])
        if path.stat().st_size>8*1024*1024:
            return {'status':'too_large','seq':message['seq']}
        with Image.open(path) as img:
            mime=Image.MIME.get(img.format)
            img.verify()
        if mime not in ('image/jpeg','image/png','image/webp','image/gif'):
            raise ValueError('Unsupported image encoding')
        return {'status':'readable','seq':message['seq'],'_image_content':{
            'type':'image','data':base64.b64encode(path.read_bytes()).decode(),'mimeType':mime}}
    selected = [m for m in messages if m['seq'] in arguments['question_seqs']]
    if selected and all(is_image_message(m) for m in selected):
        raise ValueError('An image alone cannot authorize a reply')
    return store.submit(task['id'],arguments['revision'],arguments['question_seqs'],arguments['text'],
                        available=[m['seq'] for m in messages])


@router.get(PREFIX+'/mcp')
def no_stream(request: Request):
    authorize_token(request)
    return Response(status_code=405,headers={'Allow':'POST'})


@router.post(PREFIX+'/mcp')
async def mcp_endpoint(request: Request):
    owner = authorize_token(request)
    body = await request.body()
    if len(body)>65536:
        raise HTTPException(413,'Request too large')
    try:
        data = json.loads(body)
    except ValueError:
        return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON'}})
    if not isinstance(data,dict) or data.get('jsonrpc')!='2.0' or not isinstance(data.get('method'),str):
        return JSONResponse({'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}})
    identity, method, params = data.get('id'), data['method'], data.get('params') or {}
    if identity is None and method.startswith('notifications/'):
        return Response(status_code=202)
    result = None
    try:
        store = bridge_store()
        if method=='server/discover':
            result = {'resultType':'complete','supportedVersions':['2026-07-28'],
                      'capabilities':{'tools':{},'events':{}},'instructions':INSTRUCTIONS}
        elif method=='initialize':
            result = {'protocolVersion':params.get('protocolVersion','2026-07-28'),
                'capabilities':{'tools':{},'events':{}}, 'serverInfo':{'name':'codeyun-wechat-dots','version':'1.0.0'},
                'instructions':INSTRUCTIONS}
        elif method=='ping':
            result = {}
        elif method=='tools/list':
            result = {'tools':TOOLS}
        elif method=='tools/call':
            output = call_tool(store,params['name'],params.get('arguments') or {})
            picture=output.pop('_image_content',None)
            result = {'content':[{'type':'text','text':canonical(output)}, *([picture] if picture else [])],
                      'structuredContent':output,'isError':False,'resultType':'complete'}
        elif method=='events/list':
            result = {'events':[EVENT_SCHEMA] if enabled_hooks() else []}
        elif method in ('events/subscribe','events/unsubscribe'):
            if params.get('name')!=EVENT:
                raise ValueError('Unknown event')
            arguments = params.get('arguments') or {}
            validate(arguments,EVENT_SCHEMA['inputSchema'])
            key = arguments['hook_key']
            if key not in enabled_hooks():
                raise ValueError('Group is not configured for Dots')
            if method=='events/subscribe':
                result = store.subscribe(owner,key,params['delivery'],params.get('ttlMs',86400000))
                store.record_connection_check('events_subscription','success')
            else:
                store.unsubscribe(owner,key,params['delivery']['url'])
                result = {}
        else:
            return JSONResponse({'jsonrpc':'2.0','id':identity,'error':{'code':-32601,'message':'Method not found'}})
    except CallbackError as exc:
        if method=='events/subscribe':
            store.record_connection_check('events_subscription','callback_failed')
        return JSONResponse({'jsonrpc':'2.0','id':identity,'error':{'code':-32015,'message':'Callback endpoint error','data':{'reason':str(exc)}}})
    except (ValueError,KeyError,TypeError,ValidationError):
        if method=='events/subscribe':
            store.record_connection_check('events_subscription','invalid_request')
        return JSONResponse({'jsonrpc':'2.0','id':identity,'error':{'code':-32602,'message':'Invalid parameters or stale task; read task again'}})
    return JSONResponse({'jsonrpc':'2.0','id':identity,'result':result})
