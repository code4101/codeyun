"""One in-memory CodeYun session, exposed through a local owner-only pipe.

Only the future-community wait/read/backlog/ack/reply/receipt commands exist.
No credential travels through the pipe. No files, dot RPC or generic HTTP
proxy are exposed. Disconnecting a waiting client cancels that wait while
leaving the authenticated host available for the next command.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading

MAX_REQUEST = 65536
MAX_RESPONSE = 4 * 1024 * 1024


def pipe_identity(session_id='default'):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session_id):
        raise ValueError('invalid_session_id')
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()
    tag = hashlib.sha256(sid.encode()).hexdigest()[:16]
    return rf'\\.\pipe\codeyun-future-dot-{tag}-{session_id}', sid


def dispatch(client, command, *, cancel=None):
    """Whitelist commands, not paths or identities, for the already logged-in host."""
    from scripts.wechat_dot_wait import wait_for_messages
    error = lambda code: {'status':'error','error':{'code':code,'retryable':False}}
    if not isinstance(command,dict):
        return error('invalid_command')
    operation = command.get('operation')
    schemas = {
        'read':{'cursor','limit'}, 'wait':{'cursor','limit','timeout_seconds','until_message'},
        'backlog':{'cursor','limit'}, 'acknowledge':{'source_seqs','cursor'},
        'reply':{'request_id','source_seqs','cursor','text'}, 'receipt':{'request_id','verify'},
        'status':set(), 'close':set(),
    }
    if operation not in schemas or set(command) - {'operation'} - schemas[operation]:
        return error('unsupported_command')
    if operation in {'status','close'}:
        return {'status':'session_ready' if operation=='status' else 'session_closed'}
    if 'cursor' in command and (not isinstance(command['cursor'],str) or not re.fullmatch(r'future-community:\d{1,16}',command['cursor'])):
        return error('invalid_cursor')
    if operation in {'read','wait','backlog'}:
        limit = command.get('limit',100)
        if type(limit) is not int or not 1 <= limit <= 100:
            return error('invalid_limit')
        params={'limit':limit}
        if operation=='backlog':
            params['cursor']=command.get('cursor','future-community:0')
            return client.call('GET','/backlog',params=params)
        reader=lambda cursor:client.call('GET','/messages',params={**params,**({'cursor':cursor} if cursor is not None else {})})
        if operation=='read':
            return reader(command.get('cursor'))
        timeout = command.get('timeout_seconds',25)
        until = command.get('until_message',False)
        if type(until) is not bool or type(timeout) not in (int,float) or not 0 <= timeout <= 55:
            return error('invalid_timeout')
        return wait_for_messages(reader,cursor=command.get('cursor'),timeout_seconds=None if until else timeout,cancel=cancel)
    if operation=='receipt':
        request_id=command.get('request_id')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',request_id):
            return error('invalid_request_id')
        if type(command.get('verify',False)) is not bool:
            return error('invalid_verify')
        return client.call('GET','/replies/'+request_id,params={'verify':command.get('verify',False)})
    fields = schemas[operation]
    if not fields <= command.keys():
        return error('invalid_command')
    return client.call('POST','/replies' if operation=='reply' else '/acknowledge',
                       payload={key:command[key] for key in fields})


def serve(client, *, session_id='default', ready=None, stop=None):
    """Use an explicit current-user/SYSTEM DACL and reject remote pipe clients."""
    import pywintypes
    import win32con
    import win32file
    import win32pipe
    import win32security
    name,sid=pipe_identity(session_id)
    attributes=pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR=win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f'D:P(A;;GA;;;SY)(A;;GA;;;{sid})',win32security.SDDL_REVISION_1)
    handle=win32pipe.CreateNamedPipe(name,win32pipe.PIPE_ACCESS_DUPLEX | 0x00080000,
        win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT | 0x8,
        1,MAX_RESPONSE,MAX_REQUEST,1000,attributes)
    stop=stop or threading.Event()
    try:
        if ready: ready({'status':'session_ready','session_id':session_id})
        while not stop.is_set():
            connected=False
            try:
                try:
                    win32pipe.ConnectNamedPipe(handle,None)
                except pywintypes.error as exc:
                    if exc.winerror != 535: raise  # Client connected before ConnectNamedPipe.
                connected=True
                _,raw=win32file.ReadFile(handle,MAX_REQUEST)
                command=json.loads(raw.decode('utf-8'))
                class Disconnected:
                    def is_set(self):
                        if stop.is_set(): return True
                        try: win32pipe.PeekNamedPipe(handle,0); return False
                        except pywintypes.error: return True
                    def wait(self,seconds):
                        # A dropped waiting client frees the host without discarding its login.
                        return stop.wait(seconds)
                result=dispatch(client,command,cancel=Disconnected())
                output=json.dumps(result,ensure_ascii=False).encode('utf-8')
                if len(output)>MAX_RESPONSE:
                    output=b'{"status":"error","error":{"code":"response_too_large","retryable":false}}'
                win32file.WriteFile(handle,output)
                win32file.FlushFileBuffers(handle)
                if result.get('status')=='session_closed': stop.set()
            except (pywintypes.error, ValueError, UnicodeError):
                # No request/response dump: the caller can reconnect using the same request ID.
                pass
            finally:
                if connected:
                    try: win32pipe.DisconnectNamedPipe(handle)
                    except pywintypes.error: pass
    finally:
        handle.Close()
        client.headers.clear()


def send_command(command, *, session_id='default'):
    import win32con
    import win32file
    import win32pipe
    import pywintypes
    name,_=pipe_identity(session_id)
    raw=json.dumps(command,ensure_ascii=False).encode('utf-8')
    if len(raw)>MAX_REQUEST:
        return {'status':'error','error':{'code':'request_too_large','retryable':False}}
    handle=None
    try:
        win32pipe.WaitNamedPipe(name,2000)
        handle=win32file.CreateFile(name,win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                  0,None,win32con.OPEN_EXISTING,0x00100000 | 0x00010000,None)
        win32pipe.SetNamedPipeHandleState(handle,win32pipe.PIPE_READMODE_MESSAGE,None,None)
        win32file.WriteFile(handle,raw)
        _,output=win32file.ReadFile(handle,MAX_RESPONSE)
        return json.loads(output.decode('utf-8'))
    except pywintypes.error:
        return {'status':'disconnected','error':{'code':'session_unavailable','retryable':True},
                'request_id':command.get('request_id') if isinstance(command,dict) else None}
    finally:
        if handle is not None: handle.Close()
