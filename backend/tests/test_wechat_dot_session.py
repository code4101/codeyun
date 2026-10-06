import json
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.wechat_dot_session import dispatch, serve, send_command, pipe_identity


class Client:
    def __init__(self):
        self.headers={'Authorization':'Bearer test-only-memory-session'}
        self.calls=[]
    def call(self,method,path,**kwargs):
        self.calls.append((method,path,kwargs))
        if path=='/messages': return {'status':'new_messages','messages':[{'seq':1}],'cursor':'future-community:1'}
        if path=='/backlog': return {'status':'backlog','sent':[],'in_flight':[],'unresolved':[{'seq':1}]}
        if method=='POST': return {'status':'sent','request_id':kwargs['payload'].get('request_id')}
        return {'status':'sent','verification':{'status':'unverified'}}


def test_one_authenticated_object_handles_read_reply_receipt_and_wait_without_credential_output():
    client=Client()
    commands=[{'operation':'backlog'}, {'operation':'wait','until_message':True},
        {'operation':'reply','request_id':'stable','source_seqs':[1],'cursor':'future-community:1','text':'答案'},
        {'operation':'receipt','request_id':'stable','verify':True}, {'operation':'acknowledge','source_seqs':[2],'cursor':'future-community:2'}]
    for command in commands:
        result=dispatch(client,command)
        assert 'test-only-memory-session' not in json.dumps(result)
    assert len(client.calls)==5
    assert client.calls[2][0:2]==('POST','/replies')
    assert client.calls[3][0:2]==('GET','/replies/stable')
    assert client.headers


@pytest.mark.parametrize('command',[{'operation':'login','password':'do-not-use'},
    {'operation':'read','path':'/auth/me'}, {'operation':'read','token':'do-not-use'},
    {'operation':'receipt','request_id':'../auth/me'}, {'operation':'wait','until_message':'yes'},
    {'operation':'wait','timeout_seconds':999}, {'operation':'reply','request_id':'missing'}])
def test_session_cannot_become_generic_proxy_or_credential_transport(command):
    client=Client()
    assert dispatch(client,command)['status']=='error'
    assert not client.calls


def test_session_wait_disconnect_returns_without_losing_memory_login():
    client=Client(); cancel=threading.Event(); cancel.set()
    assert dispatch(client,{'operation':'wait','until_message':True},cancel=cancel)['status']=='cancelled'
    assert client.headers and not client.calls
    assert dispatch(client,{'operation':'status'})=={'status':'session_ready'}


@pytest.mark.skipif(sys.platform!='win32',reason='Windows owner-only named pipe contract')
def test_actual_windows_pipe_reuses_one_session_and_is_scoped_to_current_user():
    import win32con
    import win32file
    import win32security
    import win32pipe
    client=Client(); session_id='test-'+uuid4().hex
    ready=threading.Event()
    thread=threading.Thread(target=lambda:serve(client,session_id=session_id,ready=lambda _:ready.set()),daemon=True)
    thread.start(); assert ready.wait(3)
    name,sid=pipe_identity(session_id)
    win32pipe.WaitNamedPipe(name,2000)
    handle=win32file.CreateFile(name,win32con.GENERIC_READ | win32con.GENERIC_WRITE | win32con.READ_CONTROL,
                               0,None,win32con.OPEN_EXISTING,0,None)
    try:
        sd=win32security.GetSecurityInfo(handle,win32security.SE_KERNEL_OBJECT,win32security.DACL_SECURITY_INFORMATION)
        acl=sd.GetSecurityDescriptorDacl()
        trustees={win32security.ConvertSidToStringSid(acl.GetAce(i)[2]) for i in range(acl.GetAceCount())}
        assert trustees=={sid,'S-1-5-18'}
        # Release the connected probe cleanly so the host can accept commands.
        win32pipe.SetNamedPipeHandleState(handle,win32pipe.PIPE_READMODE_MESSAGE,None,None)
        win32file.WriteFile(handle,b'{"operation":"status"}')
        _,response=win32file.ReadFile(handle,4096)
        assert json.loads(response)=={'status':'session_ready'}
    finally: handle.Close()
    try:
        assert send_command({'operation':'backlog'},session_id=session_id)['status']=='backlog'
        result=send_command({'operation':'wait','until_message':True},session_id=session_id)
        assert result['status']=='new_messages'
        assert client.headers and len(client.calls)==2
    finally:
        assert send_command({'operation':'close'},session_id=session_id)['status']=='session_closed'
        thread.join(3)
    assert not thread.is_alive() and not client.headers


@pytest.mark.skipif(sys.platform!='win32',reason='Windows named pipe disconnect contract')
def test_waiting_pipe_client_can_disconnect_without_discarding_host_login():
    import win32con
    import win32file
    import win32pipe
    entered=threading.Event(); release=threading.Event()
    class IdleClient(Client):
        def call(self,*args,**kwargs):
            entered.set(); assert release.wait(3)
            return {'status':'idle','cursor':'future-community:0'}
    client=IdleClient(); session_id='cancel-'+uuid4().hex; ready=threading.Event()
    thread=threading.Thread(target=lambda:serve(client,session_id=session_id,ready=lambda _:ready.set()),daemon=True)
    thread.start(); assert ready.wait(3)
    name,_=pipe_identity(session_id); win32pipe.WaitNamedPipe(name,2000)
    handle=win32file.CreateFile(name,win32con.GENERIC_READ | win32con.GENERIC_WRITE,0,None,win32con.OPEN_EXISTING,0,None)
    try:
        win32file.WriteFile(handle,b'{"operation":"wait","until_message":true}')
        assert entered.wait(3)
    finally:
        handle.Close(); release.set()
    try:
        assert send_command({'operation':'status'},session_id=session_id)['status']=='session_ready'
        assert client.headers
    finally:
        assert send_command({'operation':'close'},session_id=session_id)['status']=='session_closed'
        thread.join(3)
    assert not thread.is_alive() and not client.headers
