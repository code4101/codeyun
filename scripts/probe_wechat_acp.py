"""Isolated ACP transport experiment; never imports the WeChat sender or changes its hook.

Install @agentclientprotocol/codex-acp under the temporary experiment directory,
then run ``uv run python -m scripts.probe_wechat_acp``. Uses the existing desktop
Codex executable/default model, a temporary read-only workspace, and fresh sessions.
The adapter itself translates ACP to Codex app-server; this is not a migration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import statistics
import subprocess
import threading
import time

import xlproject.loadenv  # noqa: F401
import psutil

from backend.core.codex.escalation import resolve_codex_executable
from backend.core.codex.wechat_agent import CodexWechatClient
from backend.core.temp_paths import codeyun_temp_root


class AcpProbe:
    """Bounded newline JSON-RPC client; rejects permissions and client tool requests."""

    def __init__(self, command: list[str], cwd: Path, env: dict):
        self.lock = threading.Lock()
        self.pending = {}
        self.serial = 0
        self.updates = []
        self.stderr = []
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', bufsize=1,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=self.read_errors, daemon=True).start()

    def write(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False)+'\n')
            self.process.stdin.flush()

    def read_errors(self):
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())
            self.stderr = self.stderr[-20:]

    def read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'method' in message and 'id' in message:
                response = ({'result': {'outcome': {'outcome': 'cancelled'}}}
                            if message['method'] == 'session/request_permission' else
                            {'error': {'code': -32601, 'message': 'Probe has no client tools'}})
                self.write({'jsonrpc': '2.0', 'id': message['id'], **response})
            elif 'id' in message:
                target = self.pending.get(message['id'])
                if target:
                    target.put(message)
            elif message.get('method') == 'session/update':
                self.updates.append(message['params'])
        for target in list(self.pending.values()):
            target.put({'error': {'message': 'ACP transport exited'}})

    def request(self, method, params, timeout=60):
        self.serial += 1
        request_id = self.serial
        target = self.pending[request_id] = queue.Queue()
        try:
            self.write({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f'ACP {method} timed out') from exc
            if 'error' in response:
                raise RuntimeError(response['error'])
            return response['result']
        finally:
            self.pending.pop(request_id, None)

    def notify(self, method, params):
        self.write({'jsonrpc': '2.0', 'method': method, 'params': params})

    def close(self):
        """Only terminate this probe's process tree; never the production worker."""
        try:
            owned = psutil.Process(self.process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            owned = []
        self.process.stdin.close()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=4)
        for child in owned:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(owned, timeout=3)
        for child in alive:
            child.kill()
        _, alive = psutil.wait_procs(alive, timeout=3)
        return {'adapter_exited': self.process.poll() is not None, 'remaining_children': len(alive)}


def main():
    root = codeyun_temp_root('acp-experiment')
    workspace = root / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    adapter = root / 'node_modules/@agentclientprotocol/codex-acp/dist/index.js'
    if not adapter.is_file():
        raise RuntimeError(f'Install @agentclientprotocol/codex-acp under {root} first')
    native = CodexWechatClient()
    try:
        native.start()
        defaults = native.current_defaults()
    finally:
        native.close()
    env = dict(os.environ)
    env.pop('CODEX_THREAD_ID', None)
    env.update(CODEX_PATH=resolve_codex_executable(prefer_desktop=True), NO_BROWSER='1',
               INITIAL_AGENT_MODE='read-only', CODEX_CONFIG=json.dumps({
                   'model': defaults['model'], 'model_reasoning_effort': defaults['effort'] or 'medium',
                   'approval_policy': 'never', 'sandbox_mode': 'read-only'}))
    probe = AcpProbe([shutil.which('node'), str(adapter)], workspace, env)
    report = {'adapter': '@agentclientprotocol/codex-acp@2.1.1', 'defaults': defaults,
              'production_switched': False, 'prompts': []}
    try:
        report['initialize'] = probe.request('initialize', {'protocolVersion': 1,
            'clientCapabilities': {}, 'clientInfo': {'name': 'codeyun-isolated-probe', 'version': '1'}})
        session = probe.request('session/new', {'cwd': str(workspace), 'mcpServers': []})
        report['session'] = session
        sid = session['sessionId']
        for _ in range(3):
            start, offset = time.perf_counter(), len(probe.updates)
            result = probe.request('session/prompt', {'sessionId': sid, 'prompt': [
                {'type': 'text', 'text': '只回复：ACP链路测试通过。不要调用工具或操作文件。'}]}, timeout=90)
            text = ''.join(u['update'].get('content', {}).get('text', '')
                           for u in probe.updates[offset:]
                           if u['update'].get('sessionUpdate') == 'agent_message_chunk')
            report['prompts'].append({'seconds': time.perf_counter()-start, 'text': text, **result})
        times = [p['seconds'] for p in report['prompts']]
        report['seconds_mean_sd'] = [statistics.mean(times), statistics.stdev(times)]
        report['load'] = probe.request('session/load', {'sessionId': sid, 'cwd': str(workspace), 'mcpServers': []})
        timer = threading.Timer(0.5, lambda: probe.notify('session/cancel', {'sessionId': sid}))
        timer.start()
        try:
            report['cancel'] = probe.request('session/prompt', {'sessionId': sid, 'prompt': [
                {'type': 'text', 'text': '请写一篇较长的ACP协议技术说明，不调用工具。'}]}, timeout=30)
        finally:
            timer.cancel()
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        report['cleanup'] = probe.close()
        (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('adapter', 'defaults', 'production_switched',
                      'seconds_mean_sd', 'cancel', 'cleanup', 'error') if key in report}, ensure_ascii=False))


if __name__ == '__main__':
    main()
