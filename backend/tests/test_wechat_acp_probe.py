"""Exercise the isolated transport with a real local fake peer, without inference."""
import os
import sys

import pytest

from scripts.probe_wechat_acp import AcpProbe


def test_streaming_permission_denial_and_cleanup(tmp_path):
    peer = '''
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':
 {'sessionId':'probe','update':{'sessionUpdate':'agent_message_chunk',
 'content':{'type':'text','text':'ok'}}}}), flush=True)
print(json.dumps({'jsonrpc':'2.0','id':'permission','method':'session/request_permission',
 'params':{}}), flush=True)
permission = json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':permission['result']}), flush=True)
for line in sys.stdin:
 pass
'''
    probe = AcpProbe([sys.executable, '-u', '-c', peer], tmp_path, dict(os.environ))
    try:
        assert probe.request('session/prompt', {}, timeout=5) == {'outcome': {'outcome': 'cancelled'}}
        assert probe.updates[0]['update']['content']['text'] == 'ok'
    finally:
        assert probe.close() == {'adapter_exited': True, 'remaining_children': 0}


def test_unexpected_client_tools_rejected(tmp_path):
    peer = '''
import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':'tool','method':'fs/write_text_file','params':{}}), flush=True)
tool = json.loads(sys.stdin.readline())
print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':tool}), flush=True)
for line in sys.stdin:
 pass
'''
    probe = AcpProbe([sys.executable, '-u', '-c', peer], tmp_path, dict(os.environ))
    try:
        assert probe.request('initialize', {}, timeout=5)['error']['code'] == -32601
    finally:
        probe.close()


def test_transport_exit_unblocks_request(tmp_path):
    probe = AcpProbe([sys.executable, '-u', '-c', 'import sys; sys.stdin.readline()'],
                     tmp_path, dict(os.environ))
    try:
        with pytest.raises(RuntimeError, match='transport exited'):
            probe.request('initialize', {}, timeout=5)
    finally:
        probe.close()
