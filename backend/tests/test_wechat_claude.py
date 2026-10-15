"""Claude WeChat provider contracts with a scripted stream-json child; no model, WeChat or Codex."""
import json
import queue
import threading
import time

import pytest

from backend.core.messaging import wechat_claude as wc
from backend.core.messaging.wechat_agent import (ATTENDANCE_ACCOUNT, WechatAgentService, save_config,
                                                 switch_wechat_hook_processor)
from backend.core.messaging.wechat_agent_store import AgentStore

CANDIDATE = json.dumps({'action': 'reply', 'text': '好的', 'summary': '摘要', 'question_seqs': [1]})
IGNORE = json.dumps({'action': 'ignore', 'text': '', 'summary': '', 'question_seqs': []})


class FakeProc:
    pid = 999999  # not a live process: tree-kill is a no-op

    def __init__(self, args, script):
        self.args, self.script, self.out, self.written, self.closed = args, script, queue.Queue(), [], False
        self.session = args[args.index('--resume' if '--resume' in args else '--session-id') + 1]
        self.stdin, self.stdout = self, iter(self.lines())
        self.stderr = iter(getattr(script, 'stderr', []))
        self.emit({'type': 'system', 'subtype': 'init', 'session_id': self.session, 'model': 'claude-test'})

    def lines(self):
        while (item := self.out.get()) is not None:
            yield json.dumps(item) + '\n'

    def emit(self, event):
        self.out.put(event)

    def echo(self, text):
        self.emit({'type': 'user', 'isReplay': True,
                   'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}})

    def result(self, text, **extra):
        self.emit({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text,
                   'permission_denials': [], **extra})

    def write(self, line):
        message = json.loads(line)
        self.written.append(message)
        if message['type'] == 'user':
            self.script(self, message['message']['content'][0]['text'])

    def flush(self):
        pass

    def close(self):
        self.closed = True
        self.out.put(None)

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0 if self.closed else None


class Factory:
    def __init__(self, script):
        self.script, self.procs = script, []

    def __call__(self, args):
        proc = FakeProc(args, self.script)
        self.procs.append(proc)
        return proc


def answer(proc, text):
    proc.echo(text)
    proc.result(CANDIDATE)


def make(tmp_path, script=answer):
    factory = Factory(script)
    client = wc.ClaudeWechatClient(state_dir=tmp_path / 'claude', popen=factory, reaper=lambda entry: None)
    return client, factory


def run(client, thread, prompt='问', **kwargs):
    return client.run(thread, prompt, lambda: False, threading.Event(), lambda turn: None, timeout=5, **kwargs)


def test_json_result_new_session_then_resume_reattaches_rules(tmp_path):
    client, factory = make(tmp_path)
    thread = client.open_thread(None, '标题', instructions='业务规则')
    assert wc.is_claude_thread(thread)
    assert run(client, thread)['text'] == '好的'
    run(client, thread, '再问')
    first, second = (p.args for p in factory.procs)
    assert '--session-id' in first and '--resume' not in first
    assert '--resume' in second and '--session-id' not in second
    system = tmp_path / 'claude' / (thread[len(wc.THREAD_PREFIX):] + '.system.txt')
    assert system.read_text(encoding='utf-8').startswith('业务规则')
    for args in (first, second):  # rules are re-attached on resume, never assumed persisted
        assert args[args.index('--append-system-prompt-file') + 1] == str(system.resolve())
        assert '--dangerously-skip-permissions' not in args and 'bypassPermissions' not in args
        assert 'CronCreate' in args[args.index('--disallowedTools'):args.index('--allowedTools')]


def test_no_codex_client_is_touched(tmp_path, monkeypatch):
    import backend.core.codex.wechat_agent as codex
    monkeypatch.setattr(codex.CodexWechatClient, '__init__', lambda *a, **k: pytest.fail('Codex instantiated'))
    client, _ = make(tmp_path)
    run(client, client.open_thread(None, 't'))


def test_codex_thread_is_never_resumed(tmp_path):
    client, factory = make(tmp_path)
    with pytest.raises(wc.ProviderMismatchError):
        client.open_thread('019a-codex-thread', 't')
    with pytest.raises(wc.ProviderMismatchError):
        run(client, '019a-codex-thread')
    assert factory.procs == []


def test_invalid_output_is_repaired_once_without_tools(tmp_path):
    def script(proc, text):
        proc.echo(text)
        proc.result(CANDIDATE if '原因' in text else '随便聊聊')
    client, factory = make(tmp_path, script)
    result = run(client, client.open_thread(None, 't'))
    assert result['action'] == 'reply' and len(factory.procs) == 2
    repair_args = factory.procs[1].args
    assert 'Bash' in repair_args[repair_args.index('--disallowedTools'):] and '--allowedTools' not in repair_args


def test_permission_denial_and_error_fail_the_turn(tmp_path):
    def denied(proc, text):
        proc.echo(text)
        proc.result(CANDIDATE, permission_denials=[{'tool_name': 'Bash'}])
    client, _ = make(tmp_path, denied)
    with pytest.raises(RuntimeError, match='permission denied: Bash'):
        run(client, client.open_thread(None, 't'))

    def failed(proc, text):
        proc.echo(text)
        proc.emit({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'result': 'boom'})
    client, _ = make(tmp_path / 'b', failed)
    with pytest.raises(RuntimeError, match='boom'):
        run(client, client.open_thread(None, 't'))


def test_admission_is_proved_only_by_echo_and_survives_restart(tmp_path):
    client, _ = make(tmp_path)
    thread = client.open_thread(None, 't')
    run(client, thread, '已提交')
    restarted, factory = make(tmp_path)
    assert restarted.has_user_input(thread, '已提交')
    assert not restarted.has_user_input(thread, '从未回显')
    assert restarted.open_thread(thread, 't') == thread  # repeated recovery is idempotent
    run(restarted, thread, '续接')
    assert '--resume' in factory.procs[0].args


def test_crash_before_echo_does_not_confirm_and_creation_conflict_is_reconciled(tmp_path):
    def silent_exit(proc, text):
        proc.close()  # exits before echoing the prompt
    client, _ = make(tmp_path, silent_exit)
    thread = client.open_thread(None, 't')
    with pytest.raises(RuntimeError, match='exited'):
        run(client, thread, '丢失')
    assert not client.has_user_input(thread, '丢失')

    def popen(args):  # the crashed host had already created the session CLI-side
        if '--session-id' in args:
            proc = FakeProc(args, lambda p, t: p.close())
            proc.stderr = iter(['Error: Session ID is already in use'])
            return proc
        return FakeProc(args, answer)
    client._popen = popen
    assert run(client, thread, '重试')['action'] == 'reply'
    assert client.has_user_input(thread, '重试')


def test_soft_supplement_at_tool_boundary_and_plain_text_result(tmp_path):
    def tool_turn(proc, text):
        proc.echo(text)
        if 'soft_new_messages' not in text:
            proc.emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Read'}]}})
            proc.emit({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'x'}]}})
            threading.Timer(0.3, lambda: proc.result(CANDIDATE)).start()
    client, factory = make(tmp_path, tool_turn)
    accepted, offered = [], [[{'seq': 2}]]
    soft = lambda ok: accepted.append(1) if ok else (offered.pop() if offered else [])
    assert run(client, client.open_thread(None, 't'), soft_updates=soft)['action'] == 'reply'
    assert accepted and any('soft_new_messages' in json.dumps(m) for m in factory.procs[0].written)

    def plain(proc, text):  # no tool boundary: arrivals are offered before the result closes the turn
        proc.echo(text)
        proc.result(CANDIDATE if 'soft_new_messages' in text else IGNORE)
    client, factory = make(tmp_path / 'p', plain)
    accepted.clear()
    offered[:] = [[{'seq': 3}]]
    result = run(client, client.open_thread(None, 't'), soft_updates=soft)
    assert result['action'] == 'reply' and accepted
    assert len([m for m in factory.procs[0].written if m['type'] == 'user']) == 2


def test_unadmitted_supplement_is_left_for_the_host_to_resubmit(tmp_path, monkeypatch):
    def script(proc, text):
        if 'soft_new_messages' not in text:  # the supplement is never echoed
            proc.echo(text)
            proc.result(CANDIDATE)
    client, _ = make(tmp_path, script)
    accepted, offered = [], [[{'seq': 2}]]
    real, skew = time.monotonic, [0.0]
    monkeypatch.setattr(wc.time, 'monotonic', lambda: real() + skew[0])
    threading.Timer(0.5, lambda: skew.__setitem__(0, 30)).start()
    result = client.run(client.open_thread(None, 't'), '问', lambda: False, threading.Event(), lambda t: None,
                        timeout=500, soft_updates=lambda ok: accepted.append(1) if ok else (offered.pop() if offered else []))
    assert result['action'] == 'reply' and not accepted


def test_hard_interrupt_uses_public_control_request(tmp_path):
    def script(proc, text):
        proc.echo(text)  # the model keeps working until interrupted
    client, factory = make(tmp_path, script)
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    original = factory.script

    def interruptible(proc, text):
        original(proc, text)
    factory.script = interruptible

    def popen(args):
        proc = factory(args)
        real_write = proc.write

        def write(line):
            real_write(line)
            if json.loads(line).get('type') == 'control_request':
                proc.result('已中断', subtype='error_during_execution', is_error=True)
        proc.write = write
        return proc
    client._popen = popen
    result = client.run(client.open_thread(None, 't'), '问', flag.is_set, threading.Event(), lambda t: None, timeout=5)
    assert result == {'interrupted': True}
    assert any(m.get('request', {}).get('subtype') == 'interrupt' for m in factory.procs[0].written)


def test_compression_disables_tools_and_rejects_tool_use(tmp_path):
    summary = json.dumps({'action': 'ignore', 'text': '', 'summary': '交接摘要', 'question_seqs': []})
    client, factory = make(tmp_path, lambda p, t: (p.echo(t), p.result(summary)))
    thread = client.open_thread(None, 't')
    assert client.compress_background(thread, '旧交接', threading.Event()) == '交接摘要'
    args = factory.procs[0].args
    assert '--allowedTools' not in args and {'Bash', 'Edit', 'Write', 'Read'} <= set(args[args.index('--disallowedTools'):])

    def sneaky(proc, text):
        proc.echo(text)
        proc.emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Bash'}]}})
    client, _ = make(tmp_path / 'x', sneaky)
    with pytest.raises(RuntimeError, match='text-only'):
        client.compress_background(client.open_thread(None, 't'), '', threading.Event())


# -- host integration ---------------------------------------------------------
HOOK = {'key': 'projectgraph', 'name': 'pg', 'account_id': ATTENDANCE_ACCOUNT, 'chat_id': '123@chatroom',
        'domain': 'general', 'description': 'ProjectGraph 开发群', 'processor': 'claude', 'message_routing': True,
        'mention_aliases': ['考勤返款'], 'followup_seconds': 0}


@pytest.fixture
def store(tmp_path):
    value = AgentStore(tmp_path / 'events.sqlite')
    value.ensure_hook(HOOK['key'], HOOK['account_id'], HOOK['chat_id'])
    return value


def event(seq, text):
    return {'message_id': f'123@chatroom:{seq}', 'chat_id': HOOK['chat_id'], 'sender_id': 'user',
            'timestamp': time.time(), 'text': text, 'mentions': [ATTENDANCE_ACCOUNT]}


class FakeClaude:
    provider = 'claude'
    supports_tool_disable = True

    def __init__(self):
        self.prompts, self.opened, self.compressed = [], [], []
        self.activity_turn_ids = {}

    def open_thread(self, thread_id, title, **kwargs):
        self.opened.append(thread_id)
        return thread_id or wc.new_thread_id()

    def has_user_input(self, thread_id, prompt):
        return False

    def compress_background(self, *args, **kwargs):
        self.compressed.append(args)
        return '压缩'

    def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
        self.prompts.append(json.loads(prompt))
        on_start('turn')
        return {'action': 'reply', 'text': '好', 'summary': 'S',
                'question_seqs': [self.prompts[-1]['new_messages'][-1]['seq']]}

    def close(self):
        pass


class Source:
    def poll_updates(self, cursor):
        return {'events': [], 'cursor': cursor or {'n': 0}}


def host(store, claude):
    def codex():
        raise AssertionError('Codex client must not be constructed for a Claude group')
    return WechatAgentService({'accounts': [ATTENDANCE_ACCOUNT], 'hooks': [HOOK]}, store=store,
                              source_factory=lambda _: Source(), client_factory=codex,
                              claude_client_factory=lambda: claude, sender=lambda *a, **k: {})


def test_claude_hook_selects_claude_client_and_other_groups_keep_codex(store):
    claude = FakeClaude()
    service = host(store, claude)
    assert service.new_client(HOOK) is claude
    sentinel = object()
    service.client_factory = lambda: sentinel
    assert service.new_client({**HOOK, 'processor': 'codex'}) is sentinel
    assert service.new_client({'key': 'x'}) is claude  # Legacy domain-less groups are attendance.
    assert service.new_client({'key': 'x', 'domain': 'attendance', 'processor': 'codex'}) is claude
    assert service.new_client({'key': 'x', 'domain': 'general'}) is sentinel


def test_config_accepts_claude_only_for_plain_routed_groups(tmp_path, monkeypatch):
    import backend.core.messaging.wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    base = {'enabled': True, 'accounts': [ATTENDANCE_ACCOUNT], 'hooks': [HOOK]}
    assert save_config(base)['hooks'][0]['processor'] == 'claude'
    with pytest.raises(ValueError, match='plain routed'):
        save_config({**base, 'hooks': [{**HOOK, 'router_mode': 'flash_semantic'}]})


def test_provider_handover_keeps_cursor_summary_and_unsent_candidate(store):
    store.update_hook(HOOK['key'], thread_id='019a-codex', session_day='2999-01-01', summary='Codex交接摘要',
                      last_question_at=time.time())
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '已送达旧问题')]})
    store.update_hook(HOOK['key'], consumed_seq=1)
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 2}, 'events': [event(2, '@考勤返款 未结问题')]})
    candidate = {'action': 'reply', 'text': '待发回答', 'summary': 's', 'question_seqs': [2]}
    store.set_meta('context_delivery:' + HOOK['key'], {'thread_id': '019a-codex', 'submitted_seq': 2, 'seeded': True,
                                                      'receipts': {}, 'candidate': candidate, 'candidate_through': 2})
    claude = FakeClaude()
    service = host(store, claude)
    assert service.process_hook(HOOK, claude)
    assert claude.compressed == [] and claude.opened == [None]  # old Codex thread is neither resumed nor compressed
    prompt = claude.prompts[0]
    assert [m['seq'] for m in prompt['new_messages']] == [2]  # delivered seq 1 is not redone
    assert prompt['previous_summary'] == 'Codex交接摘要'
    assert prompt['previous_candidate'] == candidate and prompt['previous_reply_unsent']
    assert prompt['provider_handover']['from_thread_id'] == '019a-codex'
    assert wc.is_claude_thread(store.hook(HOOK['key'])['thread_id'])
    assert store.get_meta('previous_thread:' + HOOK['key']) == '019a-codex'


def test_switch_waits_for_delivered_receipt_gate_then_persists_and_reads_back(store, tmp_path, monkeypatch):
    import backend.core.messaging.wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    monkeypatch.setattr(agent, 'AgentStore', lambda path: store)
    reloads = []
    monkeypatch.setattr(agent, 'request_wechat_agent_reload', lambda: reloads.append(1) or {'status': 'scheduled'})
    save_config({'enabled': True, 'accounts': [ATTENDANCE_ACCOUNT], 'hooks': [{**HOOK, 'processor': 'codex'}]})
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '@考勤返款 问')]})
    early = switch_wechat_hook_processor(HOOK['key'], 'claude', after_seq=1)
    assert early['switched'] is False and 'answer_sent' in early['unmet']
    assert agent.load_config()['hooks'][0]['processor'] == 'codex' and not reloads
    reply = store.reserve_reply(HOOK['key'], 1, '回复')
    store.finish_reply(reply, 'sent', {'ok': True})
    done = switch_wechat_hook_processor(HOOK['key'], 'claude', after_seq=1)
    assert done['switched'] and done['processor'] == 'claude' and reloads == [1]
    assert agent.load_config()['hooks'][0]['processor'] == 'claude'
    assert switch_wechat_hook_processor(HOOK['key'], 'claude', after_seq=1)['already']


def test_unknown_write_window_forces_verification_prompt_and_session_loss_is_kept(tmp_path):
    def crash(proc, text):
        proc.close()  # the write may or may not have been executed
    client, _ = make(tmp_path, crash)
    thread = client.open_thread(None, 't')
    with pytest.raises(RuntimeError):
        run(client, thread, '改文件')
    assert client.ledger.load(thread)['intent'] and not client.has_user_input(thread, '改文件')
    seen = []
    client._popen = lambda args: FakeProc(args, lambda p, t: (seen.append(t), answer(p, t)))
    run(client, thread, '改文件')
    assert seen[0].startswith(wc.RECOVERY_NOTICE) and seen[0].endswith('改文件')
    assert client.has_user_input(thread, '改文件') and client.ledger.load(thread)['intent'] is None

    def gone(args):
        proc = FakeProc(args, lambda p, t: p.close())
        proc.stderr = iter(['No conversation found with session ID'])
        return proc
    client._popen = gone
    with pytest.raises(wc.SessionLostError):
        run(client, thread, '续')
    assert client.session_lost(thread) and client.ledger.load(thread)['lost']['detail']


def test_session_loss_hands_over_through_summary_without_recreating(store):
    claude = FakeClaude()
    claude.session_lost = lambda thread: True
    old = wc.new_thread_id()
    store.update_hook(HOOK['key'], thread_id=old, session_day='2999-01-01', summary='摘要', last_question_at=time.time())
    service = host(store, claude)  # routing baseline is taken at construction
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '@考勤返款 问')]})
    store.record_message_route(HOOK['key'], 1, 'main')
    assert service.process_hook(HOOK, claude)
    assert claude.opened == [None] and store.hook(HOOK['key'])['thread_id'] != old
    assert store.get_meta('provider_handover:' + HOOK['key'])['reason'] == 'session_lost'


def test_reverse_handover_never_resumes_claude_thread_in_codex_group(store):
    claude = FakeClaude()
    store.update_hook(HOOK['key'], thread_id=wc.new_thread_id(), session_day='2999-01-01', last_question_at=time.time())
    service = host(store, claude)
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '@考勤返款 问')]})
    store.record_message_route(HOOK['key'], 1, 'main')
    codex_hook = {**HOOK, 'processor': 'codex'}
    assert service.process_hook(codex_hook, claude)
    assert claude.opened == [None]
    assert store.get_meta('provider_handover:' + HOOK['key'])['to_provider'] == 'codex'


def test_router_thread_of_other_provider_is_replaced_not_resumed(store):
    from backend.core.messaging import wechat_message_routing as routing
    claude = FakeClaude()
    claude.run = lambda *a, **k: {'action': 'reply', 'text': '快答', 'question_seqs': [1]}
    claude.current_defaults = lambda: {'model': 'm'}
    service = host(store, claude)
    store.set_meta('message_router_thread:' + HOOK['key'], '019a-old-codex-router')
    routing.classify(service, HOOK, claude, {'seq': 1}, [])
    assert claude.opened == [None]
    assert store.get_meta('message_router_thread_previous:' + HOOK['key'])['thread_id'] == '019a-old-codex-router'
    assert wc.is_claude_thread(store.get_meta('message_router_thread:' + HOOK['key']))
    claude.opened.clear()  # a native claude router thread is kept
    kept = store.get_meta('message_router_thread:' + HOOK['key'])
    routing.classify(service, HOOK, claude, {'seq': 1}, [])
    assert claude.opened == [kept]


def test_claude_group_capability_limit_precedes_any_send(store, monkeypatch):
    from backend.core.messaging import wechat_capabilities as cap
    from backend.core.messaging import wechat_resource_acquisition as acquisition
    from backend.core.messaging.wechat_delivery import ReplyPlan, deliver_reply
    text, limited = cap.drop_unsupported_attachments(HOOK, '结果如下\nCODECLAW_IMAGE: C:/x.png\n完毕')
    assert limited and 'CODECLAW' not in text and cap.GUI_LIMIT_NOTE in text and 'x.png' not in text
    assert cap.drop_unsupported_attachments({**HOOK, 'processor': 'codex'}, 'CODECLAW_FILE: C:/x')[1] is False
    sent = []
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '问')]})
    plan = ReplyPlan([{'text': '正文', 'question_seqs': []}], [('text', '正文'), ('file', 'C:/x')], set(), [])
    with pytest.raises(ValueError, match='GUI attachments'):
        deliver_reply(store, HOOK, 1, '正文', plan, sender=lambda *a, **k: sent.append(a) or {})
    assert sent == [] and store.uncertain_replies(HOOK['key']) == []
    with pytest.raises(PermissionError):
        acquisition.acquire_message_resources(ATTENDANCE_ACCOUNT, HOOK['chat_id'], 1, allow_gui=True,
                                              allow_codex_executor=False)
    monkeypatch.setenv(cap.PROVIDER_ENV, 'claude')
    with pytest.raises(PermissionError):  # strict by default inside the Claude child
        acquisition.acquire_message_resources(ATTENDANCE_ACCOUNT, HOOK['chat_id'], 1, allow_gui=True)


def test_request_switch_is_persisted_idempotent_and_resumes_reload(store, tmp_path, monkeypatch):
    import backend.core.messaging.wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    monkeypatch.setattr(agent, 'AgentStore', lambda path: store)
    save_config({'enabled': True, 'accounts': [ATTENDANCE_ACCOUNT], 'hooks': [{**HOOK, 'processor': 'codex'}]})
    first = agent.request_wechat_hook_processor_switch(HOOK['key'], 'claude', after_seq=1, start_worker=False)
    assert first['status'] == 'scheduled' and first['after_seq'] == 1
    assert store.get_meta('processor_switch_request:' + HOOK['key'])['processor'] == 'claude'
    # gate unmet -> worker blocks with an explicit reason and never edits config
    store.set_meta('processor_switch_request:' + HOOK['key'], {**first, 'deadline': 0})
    blocked = agent.run_wechat_hook_processor_switch(HOOK['key'], poll=0)
    assert blocked['status'] == 'blocked' and 'gate not met' in blocked['reason']
    assert agent.load_config()['hooks'][0]['processor'] == 'codex'
    # other group busy: config is written but reload times out -> blocked, not effective
    store.ingest(ATTENDANCE_ACCOUNT, {'cursor': {'n': 1}, 'events': [event(1, '@考勤返款 问')]})
    store.finish_reply(store.reserve_reply(HOOK['key'], 1, '回复'), 'sent', {'ok': True})
    store.set_meta('processor_switch_request:' + HOOK['key'], {**first, 'deadline': time.time() + 60})
    calls = []

    def busy(timeout=0):
        calls.append(1)
        raise TimeoutError('other group busy')
    monkeypatch.setattr(agent, 'reload_wechat_agent_service_when_idle', busy)
    blocked = agent.run_wechat_hook_processor_switch(HOOK['key'], poll=0)
    assert blocked['status'] == 'blocked' and 'other group busy' in blocked['reason']
    assert agent.load_config()['hooks'][0]['processor'] == 'claude'
    assert not agent.wechat_hook_processor_switch_status(HOOK['key'])['effective']
    # repeated call: config already claude, but the reload is retried instead of reporting done
    monkeypatch.setattr(agent, 'reload_wechat_agent_service_when_idle', lambda timeout=0: calls.append(2) or {})
    monkeypatch.setattr(agent, 'processor_switch_effective', lambda *a, **k: len(calls) > 1)
    done = agent.run_wechat_hook_processor_switch(HOOK['key'], poll=0)
    assert calls == [1, 2] and done['status'] == 'effective'
