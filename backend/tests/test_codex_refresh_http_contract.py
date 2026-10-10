"""HTTP contracts only: no live database, device, native source or remote calls."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.api import codex_sessions as codex, device_entries as entries
from backend.core.codex.sessions import CodexCacheRefreshBusyError, CodexCacheSourceChangedError
from backend.models import UserDevice


@pytest.fixture
def client(monkeypatch):
    app=FastAPI()
    app.include_router(codex.router,prefix='/api/codex')
    app.include_router(entries.router,prefix='/api/device-entries')
    app.dependency_overrides[codex.get_session]=lambda:object()
    app.dependency_overrides[codex.verify_api_token]=lambda:object()
    app.dependency_overrides[entries.get_current_user_from_token]=lambda:SimpleNamespace(id=1)
    entry=UserDevice(entry_id='device',user_id=1,mode='local')
    monkeypatch.setattr(entries,'_get_entry_or_404',lambda *args:entry)
    with TestClient(app) as http:
        yield http,entry


@pytest.mark.parametrize('error_type',[CodexCacheRefreshBusyError,CodexCacheSourceChangedError])
@pytest.mark.parametrize('prefix',['/api/codex','/api/device-entries/device/codex'])
@pytest.mark.parametrize('endpoint,provider',[
    ('/threads/thread','build_codex_thread_detail'),('/overview','build_codex_overview'),
    ('/workload','build_codex_workload')])
def test_local_refresh_deferrals_are_structured_503_without_private_details(client,monkeypatch,error_type,prefix,endpoint,provider):
    http,_=client;calls=[]
    module=codex if prefix=='/api/codex' else entries
    def fail(*args,**kwargs):
        calls.append((args,kwargs))
        raise error_type('C:/private/owner/.codex/state_5.sqlite SELECT secret FROM private_table')
    monkeypatch.setattr(module,provider,fail)
    response=http.get(prefix+endpoint)
    assert response.status_code==503
    detail=response.json()['detail']
    assert set(detail)=={'code','message','retryable'}
    assert detail['code']==error_type.code and detail['retryable'] is True
    assert detail['message'] and 'private' not in response.text and 'SELECT' not in response.text
    assert len(calls)==1


@pytest.mark.parametrize('prefix',['/api/codex','/api/device-entries/device/codex'])
def test_corrupt_cache_remains_500_not_retryable_and_does_not_leak_sql(client,monkeypatch,prefix):
    http,_=client;module=codex if prefix=='/api/codex' else entries
    def fail(*args,**kwargs):
        raise OperationalError('SELECT private FROM owner_path',{},sqlite3.DatabaseError('database disk image is malformed C:/private/cache.sqlite'))
    monkeypatch.setattr(module,'build_codex_thread_detail',fail)
    response=http.get(prefix+'/threads/thread')
    assert response.status_code==500
    assert response.json()=={'detail':'读取 Codex 会话失败'}
    assert 'private' not in response.text


@pytest.mark.parametrize('status,code',[(503,'refresh_busy'),(503,'refresh_source_changed'),(500,'corrupt_cache')])
@pytest.mark.parametrize('endpoint',['/threads/thread','/overview','/workload'])
def test_remote_proxy_preserves_status_structured_body_and_makes_one_request(client,monkeypatch,status,code,endpoint):
    http,entry=client
    entry.mode='remote';entry.server_url='https://owned-test.invalid';entry.token='test-only'
    body={'detail':{'code':code,'message':'remote diagnostic','retryable':status==503}}
    remote=requests.Response();remote.status_code=status
    remote.headers['Content-Type']='application/json'
    remote._content=json.dumps(body).encode()
    calls=[]
    def request(**kwargs):calls.append(kwargs);return remote
    monkeypatch.setattr(entries.requests,'request',request)
    def no_cache(*args,**kwargs):pytest.fail('remote error response was cached as success')
    monkeypatch.setattr(entries,'cache_remote_codex_thread_detail',no_cache)
    monkeypatch.setattr(entries,'cache_remote_codex_overview',no_cache)
    response=http.get('/api/device-entries/device/codex'+endpoint)
    assert response.status_code==status and response.json()==body
    assert len(calls)==1
    assert calls[0]['method']=='GET'
    assert calls[0]['url']=='https://owned-test.invalid/api/codex'+endpoint


def detail_payload():
    return {'root_dir':'/root','thread':{'id':'thread','title':'Title','project_label':'Project',
        'group_key':'group','group_label':'Group'},'message_count':1,'user_message_count':1,
        'assistant_message_count':0,'messages':[{'seq':1,'role':'user','text':'hello'}]}


@pytest.mark.parametrize('prefix',['/api/codex','/api/device-entries/device/codex'])
def test_local_success_detail_contract_is_compatible(client,monkeypatch,prefix):
    http,_=client;module=codex if prefix=='/api/codex' else entries
    payload=detail_payload()
    monkeypatch.setattr(module,'build_codex_thread_detail',lambda *a,**kw:payload)
    response=http.get(prefix+'/threads/thread')
    assert response.status_code==200
    expected=codex.CodexThreadDetailResponse.model_validate(payload).model_dump() if module is codex else payload
    assert response.json()==expected


def test_remote_success_keeps_existing_body_and_cache_flow(client,monkeypatch):
    http,entry=client
    entry.mode='remote';entry.server_url='https://owned-test.invalid';entry.token='test-only'
    payload=detail_payload()
    remote=requests.Response();remote.status_code=200;remote.headers['Content-Type']='application/json'
    remote._content=json.dumps(payload).encode()
    requests_seen=[];cached=[]
    monkeypatch.setattr(entries.requests,'request',lambda **kw:requests_seen.append(kw) or remote)
    monkeypatch.setattr(entries,'cache_remote_codex_thread_detail',lambda *a,**kw:cached.append((a,kw)))
    response=http.get('/api/device-entries/device/codex/threads/thread')
    assert response.status_code==200 and response.json()==payload
    assert len(requests_seen)==1 and len(cached)==1
    assert cached[0][0]==('device',payload)
