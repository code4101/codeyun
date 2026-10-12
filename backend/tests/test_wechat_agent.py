"""Deterministic delivery/routing contracts; real AI and WeChat verified separately."""
from datetime import datetime
import json
import threading
import time

import pytest

from backend.core.messaging.wechat_agent import WechatAgentService, is_hard, ATTENDANCE_ACCOUNT
from backend.core.messaging.wechat_agent_store import AgentStore, SHANGHAI, should_roll_session
from pyxllib.autogui.wechat_updates import mention_ids, committed_wal_frames


HOOK = {"key": "test", "account_id": ATTENDANCE_ACCOUNT, "chat_id": "123@chatroom", "mention_aliases": ["考勤返款"]}


def message(seq, text="问题", *, sender="user", mentions=None):
    return {"message_id": f"123@chatroom:{seq}", "chat_id": HOOK["chat_id"], "sender_id": sender,
            "timestamp": time.time(), "text": text, "mentions": mentions or []}


def ingest(store, *messages):
    return store.ingest(ATTENDANCE_ACCOUNT, {"cursor": {"n": len(messages)}, "events": list(messages)})


@pytest.fixture
def store(tmp_path):
    value = AgentStore(tmp_path / "events.sqlite")
    value.ensure_hook(**{k: HOOK[k] for k in ("key", "account_id", "chat_id")})
    return value


def test_replay_cursor_and_task_progress_are_atomic_and_recoverable(store):
    event = message(1)
    assert ingest(store, event) == 1
    assert ingest(store, event) == 0
    assert len(store.messages("test")) == 1
    store.update_hook("test", status="running", turn_id="old-turn")
    recovered = AgentStore(store.path)
    recovered.recover()
    assert recovered.hook("test")["status"] == "idle"
    assert recovered.hook("test")["consumed_seq"] == 0
    assert recovered.get_meta("cursor:" + ATTENDANCE_ACCOUNT) == {"n": 1}


def test_reply_rejects_unseen_message_and_ignores_self_echo(store):
    ingest(store, message(1))
    ingest(store, message(2, "补充"))
    assert store.reserve_reply("test", 1, "旧回复") is None
    ingest(store, message(3, "自己的回声", sender=ATTENDANCE_ACCOUNT))
    reply = store.reserve_reply("test", 2, "新回复")
    assert reply
    store.finish_reply(reply, "sent", {"ok": True})
    assert store.reserve_reply("test", 2, "重复") is None
    assert store.hook("test")["consumed_seq"] == 2
    assert store.messages("test", 2) == []


def test_crash_during_send_is_uncertain_never_blindly_replayed(store):
    ingest(store, message(1))
    assert store.reserve_reply("test", 1, "回复")
    store.recover()
    assert store.hook("test")["status"] == "uncertain"
    assert store.reserve_reply("test", 1, "回复") is None
    assert store.status()["outbox"][0]["status"] == "uncertain"


def test_explicit_delivery_reconciliation_can_release_confirmed_unsent_reply(store):
    ingest(store, message(1))
    reply_id = store.reserve_reply("test", 1, "回复")
    store.finish_reply(reply_id, "uncertain", {"error": "timeout"})
    with pytest.raises(ValueError, match="evidence"):
        store.reconcile_reply(reply_id, sent=False, evidence="")
    store.reconcile_reply(reply_id, sent=False, evidence="发送提供方确认调用未进入微信发送函数")
    assert store.reserve_reply("test", 1, "复查后的回复") == reply_id


def test_unrelated_soft_event_cannot_erase_unsent_reply(store):
    ingest(store, message(1, "@考勤返款 请查"))
    source = Source(store, lambda: ingest(store, message(2, "无关闲聊")))
    class IgnoringClient(CandidateClient):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            if len(self.prompts) > 1:
                return {"action": "ignore", "text": "", "summary": "忽略无关消息", "question_seqs": []}
            return result
    sent = []
    assert service(store, source, lambda *a, **k: sent.append(a) or {}).process_hook(HOOK, IgnoringClient())
    assert sent == [(HOOK["chat_id"], "初稿")]


def test_mentions_structured_or_exact_leading_alias_only():
    source = "<msgsource><atuserlist><![CDATA[wxid_a,wxid_b]]></atuserlist></msgsource>"
    assert mention_ids(source) == ["wxid_a", "wxid_b"]
    assert is_hard(message(1, mentions=[ATTENDANCE_ACCOUNT]), HOOK)
    assert is_hard(message(2, "@考勤返款\u2005问题"), HOOK)
    assert not is_hard(message(3, "他们说 @考勤返款 问题"), HOOK)
    assert not is_hard(message(4, "@考勤返款另一个人 问题"), HOOK)
    assert not is_hard(message(5, "@考勤返款 问题", sender=ATTENDANCE_ACCOUNT), HOOK)
    assert mention_ids("<broken>") == []


def test_new_thread_reads_pre_listener_history_without_replaying_old_question(store):
    ingest(store, message(1, "@考勤返款 统计空数据"))
    class HistorySource(Source):
        def list_messages(self, chat, **kwargs):
            assert chat == HOOK["chat_id"] and kwargs["include_resources"] is False
            return {"items": [{"local_id": 900, "create_time": 1, "message_text": "51届觉观，总人数66", "sender_username": "owner"},
                              {"local_id": 1, "create_time": time.time() + 60, "message_text": "尚未入队"}]}
    svc, client = service(store, HistorySource(store), lambda *a, **k: {}), CandidateClient()
    assert svc.process_hook(HOOK, client)
    context = client.prompts[0]["context"]
    assert context[0]["history_only"] and "seq" not in context[0]
    assert "51届" in context[0]["text"]
    assert [row["seq"] for row in client.prompts[0]["new_messages"]] == [1]


@pytest.mark.parametrize("thread_id", [None, "existing-thread"])
@pytest.mark.parametrize("alias,expected", [("default", None), ("priority", "fast")])
def test_desktop_tier_is_normalized_only_after_exact_cli_rejection(monkeypatch, thread_id, alias, expected):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, "start", lambda: None)
    def rpc(method, params):
        calls.append((method, json.loads(json.dumps(params))))
        if method == "thread/name/set":
            return {}
        if "service_tier" not in params["config"]:
            raise RuntimeError(f'config.toml:4:16: unknown variant `{alias}`, expected `fast` or `flex`')
        return {"thread": {"id": thread_id or "new-thread"}}
    monkeypatch.setattr(client, "rpc", rpc)
    assert client.open_thread(thread_id, "daily") == (thread_id or "new-thread")
    assert "service_tier" not in calls[0][1]["config"]
    assert calls[1][1]["config"]["service_tier"] == expected
    assert calls[0][0] == ("thread/resume" if thread_id else "thread/start")


def test_unrelated_codex_configuration_error_is_not_silently_overridden(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    monkeypatch.setattr(client, "start", lambda: None)
    monkeypatch.setattr(client, "rpc", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model not found")))
    with pytest.raises(RuntimeError, match="model not found"):
        client.open_thread(None, "daily")


def test_desktop_writer_conflict_forks_history_instead_of_retrying_resume(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, 'start', lambda: None)
    def rpc(method, params):
        calls.append((method, dict(params)))
        if method == 'thread/resume':
            raise RuntimeError("Codex thread/resume: thread old already has an active writer")
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'idle'}, 'turns': [{'status': 'completed'}]}}
        if method == 'thread/fork':
            return {'thread': {'id': 'forked'}}
        return {}
    monkeypatch.setattr(client, 'rpc', rpc)
    assert client.open_thread('old','WeChat',private=True)=='forked'
    assert [c[0] for c in calls]==['thread/resume','thread/read','thread/goal/get','thread/fork','thread/name/set']
    assert calls[3][1]['threadId']=='old'
    assert calls[3][1]['config']==calls[0][1]['config']


@pytest.mark.parametrize('active', [False, True])
def test_schema_recovery_forks_only_after_public_history_proves_idle(monkeypatch, active):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, 'start', lambda: None)
    def rpc(method, params):
        calls.append(method)
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'notLoaded'}, 'turns': [
                {'status': 'inProgress' if active else 'interrupted'}]}}
        if method == 'thread/fork':
            return {'thread': {'id': 'recovered'}}
        return {}
    monkeypatch.setattr(client, 'rpc', rpc)
    if active:
        with pytest.raises(RuntimeError, match='active work'):
            client.open_thread('old', 'WeChat', recover_output_schema=True)
        assert calls == ['thread/goal/get', 'thread/read']
    else:
        assert client.open_thread('old', 'WeChat', recover_output_schema=True) == 'recovered'
        assert calls == ['thread/goal/get', 'thread/read', 'thread/goal/get', 'thread/fork', 'thread/name/set']


@pytest.mark.parametrize('condition', ['live', 'wrong_worker', 'wrong_thread', 'completed', 'pending', 'lost_ack'])
def test_autonomous_goal_supervision_requires_live_ownership_and_keeps_inbox(store, condition):
    import os
    svc = service(store)
    store.update_hook('test', thread_id='daily', status='idle')
    store.set_meta('supervision:test', {'supervisor_thread_id': 'manager',
        'updated_at': 10, 'instructions': '优先核验领奖，再继续下一轮'})
    store.set_meta('turn_activity:test', {'thread_id': 'other' if condition == 'wrong_thread' else 'daily',
        'turn_id': 'goal', 'worker_pid': -1 if condition == 'wrong_worker' else os.getpid(),
        'state': 'completed' if condition == 'completed' else 'inProgress'})
    if condition == 'pending':
        ingest(store, message(1, '新的所有者交办'))
    calls = []
    class Client:
        def rpc(self, method, params):
            calls.append((method, params))
            if condition == 'lost_ack':
                raise RuntimeError('acknowledgement lost')
            return {}
        def has_user_input(self, thread_id, prompt):
            return thread_id == 'daily' and prompt == calls[-1][1]['input'][0]['text']
    expected = condition in {'live', 'lost_ack'}
    assert svc.steer_autonomous_supervision(HOOK, Client()) is expected
    assert store.hook('test')['consumed_seq'] == 0
    assert not svc.steer_autonomous_supervision(HOOK, Client())
    assert len(calls) == int(expected)
    if expected:
        assert calls[0][0] == 'turn/steer'
        assert calls[0][1]['expectedTurnId'] == 'goal'
        assert 'outputSchema' not in calls[0][1]


def test_supervised_execution_lease_preserves_pending_until_release_or_expiry(store, monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: store.path.parent)
    monkeypatch.setattr(agent, 'load_config', lambda: {'hooks': [HOOK]})
    store.update_hook('test', thread_id='daily')
    ingest(store, message(1, mentions=[ATTENDANCE_ACCOUNT]))
    svc, client = service(store, sender=lambda *a, **k: {}), CandidateClient()
    with pytest.raises(ValueError, match='existing hook thread'):
        agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='manager', thread_id='other')
    lease = agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='manager', thread_id='daily')
    status = agent.describe_health({'running': True, **store.status()}, {'hooks': [HOOK]})
    assert status['health'] == 'busy' and status['hooks'][0]['main_execution_delegated']
    assert status['hooks'][0]['execution_owner'] == lease
    assert not svc.process_hook(HOOK, client)
    assert not client.prompts and store.hook('test')['consumed_seq'] == 0
    assert len(store.pending_messages('test')) == 1
    store.set_meta('execution_owner:test', {**lease, 'expires_at': 0})
    assert not store.status()['hooks'][0]['main_execution_delegated']
    assert svc.process_hook(HOOK, client)
    record = agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='manager', thread_id='')
    assert record['expires_at'] == 0


def test_owner_supervision_is_read_back_and_included_without_synthetic_messages(store, monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: store.path.parent)
    monkeypatch.setattr(agent, 'load_config', lambda: {'hooks': [HOOK]})
    record = agent.set_wechat_agent_supervision('test', supervisor_thread_id='manager',
                                               instructions='先完成到期作业，非操作分析可并行。')
    assert store.get_meta('supervision:test') == record
    svc = service(store, sender=lambda *a, **k: {})
    ingest(store, message(1, mentions=[ATTENDANCE_ACCOUNT]))
    client = CandidateClient()
    svc.process_hook(HOOK, client)
    assert client.prompts[0]['owner_authorized_supervision'] == record
    assert store.status()['event_count'] == 1
    assert store.status()['hooks'][0]['turn_activity']['turn_id'] == 'turn'
    with pytest.raises(ValueError, match='Unknown'):
        agent.set_wechat_agent_supervision('absent', supervisor_thread_id='manager', instructions='x')


@pytest.mark.parametrize('next_turn', [False, True])
def test_start_ack_does_not_rewind_reader_terminal_or_new_goal(store, next_turn):
    svc = service(store)
    class Client:
        activity_turn_ids = {}
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
            turn_id = 'goal' if next_turn else 'reply'
            self.activity_turn_ids[thread_id] = turn_id
            kwargs['on_activity']({'thread_id': thread_id, 'turn_id': turn_id,
                'state': 'inProgress' if next_turn else 'completed',
                'event': 'turn/started' if next_turn else 'turn/completed'})
            on_start('reply')
            return {'action': 'ignore'}
    svc.run_with_receipt('test', {'thread_id': 'daily', 'submitted_seq': 0}, Client(),
        'task', 0, lambda: False, lambda _: None)
    record = store.get_meta('turn_activity:test')
    assert record['turn_id'] == ('goal' if next_turn else 'reply')
    assert record['state'] == ('inProgress' if next_turn else 'completed')


def test_supervisor_update_steers_once_without_consuming_inbox(store):
    svc, client = service(store, sender=lambda *a, **k: {}), CandidateClient()
    ingest(store, message(1, mentions=[ATTENDANCE_ACCOUNT]))
    original_run = client.run
    def run(*args, **kwargs):
        result = original_run(*args, **kwargs)
        record = {'supervisor_thread_id': 'manager', 'updated_at': time.time(),
                  'instructions': '先验收当前真实结果。'}
        store.set_meta('supervision:test', record)
        steer = kwargs['soft_updates'](False)
        assert json.loads(steer)['owner_authorized_supervision'] == record
        assert store.hook('test')['consumed_seq'] == 0
        kwargs['soft_updates'](True)
        assert kwargs['soft_updates'](False) == []
        return result
    client.run = run
    assert svc.process_hook(HOOK, client)
    assert store.status()['event_count'] == 1
    assert store.hook('test')['consumed_seq'] == 1


def test_writer_fork_persists_new_hook_identity_and_replays_only_pending_input(store):
    svc, hook=private_service(store)
    ingest(store,owner_event(1,'已完成'),owner_event(2,'新问题'))
    store.update_hook('owner',consumed_seq=1,thread_id='desktop',
                      session_day=datetime.now(SHANGHAI).date().isoformat())
    store.set_meta('context_delivery:owner',{'thread_id':'desktop','submitted_seq':1,
                   'seeded':True,'receipts':{},'candidate':None})
    class ForkedClient(PlainClient):
        def open_thread(self,*args,**kwargs): return 'background'
    client=ForkedClient()
    assert svc.process_hook(hook,client)
    assert store.hook('owner')['thread_id']=='background'
    assert store.get_meta('previous_thread:owner')=='desktop'
    assert '新问题' in client.prompts[0] and '已完成' not in client.prompts[0]


def test_backend_start_reuses_independent_worker_and_launches_only_when_missing(tmp_path, monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    from backend.core.services import launcher
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    monkeypatch.setattr(agent, 'load_config', lambda: {'enabled': True})
    monkeypatch.setattr(agent, '_service', None)
    states=iter([{'running':True}])
    monkeypatch.setattr(agent, 'wechat_agent_status', lambda: next(states))
    launched=[]
    class Process:
        def poll(self): return None
    monkeypatch.setattr(launcher, 'popen_service', lambda command, **kw: launched.append((command, kw)) or Process())
    assert agent.start_wechat_agent_service()['running']
    assert not launched
    states=iter([{'running':False},{'running':True}])
    assert agent.start_wechat_agent_service()['running']
    assert len(launched)==1 and launched[0][0][-1]=='run'
    assert launched[0][0][-2].endswith('wechat_agent.py')
    assert agent._service is None


def test_operator_stop_requests_graceful_external_worker_exit(tmp_path, monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: tmp_path)
    monkeypatch.setattr(agent, '_service', None)
    monkeypatch.setattr(agent, 'wechat_agent_status', lambda: {'running':False})
    assert agent.stop_wechat_agent_service()=={'running':False}
    assert AgentStore(tmp_path/'events.sqlite').get_meta('stop_requested')>0


def test_polling_process_with_failed_active_hook_is_degraded():
    from backend.core.messaging.wechat_agent import describe_health
    state = {"running": True, "hooks": [{"key": "live", "status": "failed", "last_error": "configuration rejected"},
                                        {"key": "old-test", "status": "failed", "last_error": "unused"}]}
    result = describe_health(state, {"hooks": [{"key": "live"}]})
    assert result["health"] == "degraded" and result["errors"] == ["configuration rejected"]
    state["hooks"][0]["status"] = "idle"
    assert describe_health(state, {"hooks": [{"key": "live"}]})["health"] == "ready"


def test_resumed_daily_thread_rereads_default_model_on_every_turn(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, turns = CodexWechatClient(), []
    models = iter([("first-default", "medium"), ("changed-default", "high")])
    def rpc(method, params):
        if method == "config/read":
            model, effort = next(models)
            return {"config": {"model": model, "model_reasoning_effort": effort}}
        assert method == "turn/start"
        turns.append(params)
        turn_id = str(len(turns))
        client.events.put({"method": "item/completed", "params": {"threadId": "daily", "item": {
            "type": "agentMessage", "text": json.dumps({"action": "ignore", "text": "", "summary": "", "question_seqs": []})}}})
        client.events.put({"method": "turn/completed", "params": {"threadId": "daily", "turn": {
            "id": turn_id, "status": "completed"}}})
        return {"turn": {"id": turn_id}}
    monkeypatch.setattr(client, "rpc", rpc)
    for _ in range(2):
        client.run("daily", "question", lambda: False, threading.Event(), lambda turn: None)
    assert [turn["threadId"] for turn in turns] == ["daily", "daily"]
    assert [(turn["model"], turn["effort"]) for turn in turns] == [("first-default", "medium"), ("changed-default", "high")]


def test_default_model_failure_never_falls_back(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    def rpc(method, params):
        calls.append(method)
        if method == "config/read":
            return {"config": {"model": "current-default"}}
        raise RuntimeError("current-default not supported")
    monkeypatch.setattr(client, "rpc", rpc)
    with pytest.raises(RuntimeError, match="current-default not supported"):
        client.run("daily", "question", lambda: False, threading.Event(), lambda turn: None)
    assert calls == ["config/read", "turn/start"]


def test_legacy_pinned_model_is_removed_from_saved_configuration(tmp_path, monkeypatch):
    import backend.core.messaging.wechat_agent as module
    monkeypatch.setattr(module, "agent_root", lambda: tmp_path)
    config = {"accounts": [ATTENDANCE_ACCOUNT], "hooks": [HOOK], "codex_model": "legacy-model"}
    (tmp_path / "config.json").write_text(json.dumps(config), encoding="utf-8")
    assert "codex_model" not in module.load_config()
    module.save_config(config)
    assert "codex_model" not in json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))


def test_recovery_after_thread_start_failure_keeps_original_question_and_sends_once(store):
    ingest(store, message(1, "@考勤返款 统计比例"))
    class RecoveringClient(CandidateClient):
        failures = 1
        def open_thread(self, *args):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("configuration rejected")
            return super().open_thread(*args)
    sent, client = [], RecoveringClient()
    svc = service(store, sender=lambda *a, **k: sent.append(a) or {})
    with pytest.raises(RuntimeError):
        svc.process_hook(HOOK, client)
    assert store.hook("test")["consumed_seq"] == 0
    assert svc.process_hook(HOOK, client)
    assert len(sent) == 1
    assert not svc.process_hook(HOOK, client)


def test_daily_sessions_respect_midnight_and_two_hour_boundary():
    def stamp(text):
        return datetime.fromisoformat(text).replace(tzinfo=SHANGHAI).timestamp()
    last = stamp("2026-10-02T23:30:00")
    assert not should_roll_session("2026-10-02", last, stamp("2026-10-03T00:20:00"))
    assert not should_roll_session("2026-10-02", last, stamp("2026-10-03T01:29:59"))
    assert should_roll_session("2026-10-02", last, stamp("2026-10-03T01:30:00"))
    assert not should_roll_session("2026-10-03", stamp("2026-10-03T08:00:00"), stamp("2026-10-03T18:00:00"))


@pytest.mark.parametrize("domain", ["attendance", "fanxiu", "general"])
def test_cross_day_unmentioned_question_routes_before_new_daily_thread(store, domain):
    ingest(store, message(1, "补充昨天的问题"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01", last_question_at=time.time() - 7201)
    hook = {**HOOK, "followup_seconds": 0, "domain": domain}
    class RoutingClient(CandidateClient):
        def __init__(self):
            super().__init__()
            self.opened = []
        def open_thread(self, thread_id, title):
            self.opened.append(thread_id)
            return super().open_thread(thread_id, title)
    client = RoutingClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(hook, client)
    assert client.opened == ["yesterday", None]
    assert store.hook("test")["thread_id"] == "daily-thread"


@pytest.mark.parametrize("domain", ["attendance", "fanxiu", "general"])
def test_cross_day_unrelated_chatter_does_not_create_new_thread(store, domain):
    ingest(store, message(1, "无关闲聊"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01", last_question_at=time.time() - 7201)
    class IgnoreClient(CandidateClient):
        def run(self, *args, **kwargs):
            return {"action": "ignore", "text": "", "summary": "", "question_seqs": []}
    assert service(store).process_hook({**HOOK, "followup_seconds": 0, "domain": domain}, IgnoreClient())
    assert store.hook("test")["thread_id"] == "yesterday"


def test_new_hook_baselines_existing_inbox_without_history_replay(store):
    ingest(store, message(1))
    store.ensure_hook("new-subscription", ATTENDANCE_ACCOUNT, HOOK["chat_id"])
    assert store.messages("new-subscription", store.hook("new-subscription")["consumed_seq"]) == []
    ingest(store, message(2))
    assert len(store.messages("new-subscription", store.hook("new-subscription")["consumed_seq"])) == 1


class Source:
    def __init__(self, store, callback=None):
        self.store, self.callback = store, callback
    def poll_updates(self, cursor):
        if self.callback:
            callback, self.callback = self.callback, None
            callback()
        return {"events": [], "cursor": cursor or {"n": 0}}


class CandidateClient:
    def __init__(self, callback=None):
        self.prompts, self.callback = [], callback
    def open_thread(self, thread_id, title):
        return thread_id or "daily-thread"
    def compress_background(self, thread_id, previous_summary, stop_event, **kwargs):
        return "压缩交接：未结问题及已核验结果"
    def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
        self.prompts.append(json.loads(prompt))
        on_start("turn")
        if self.callback:
            callback, self.callback = self.callback, None
            callback(hard_interrupt)
        return {"action": "reply", "text": "已纳入补充" if len(self.prompts) > 1 else "初稿", "summary": "未结摘要", "question_seqs": [1]}


def test_existing_specialist_group_receives_general_question_policy_every_turn(store):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, 'domain': 'general', 'description': '专门开发 ProjectGraph',
            'priority_sender_ids': [OWNER_ACCOUNT]}
    store.update_hook('test', thread_id='existing', session_day=datetime.now(SHANGHAI).date().isoformat(),
                      last_question_at=time.time())
    store.set_meta('context_delivery:test', {'thread_id': 'existing', 'submitted_seq': 0,
                    'seeded': True, 'receipts': {}, 'candidate': None})
    ingest(store, message(1, '国家图书馆附近有什么好吃的？', sender=OWNER_ACCOUNT))
    client = CandidateClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(hook, client)
    assert 'group_context' not in client.prompts[0]  # Existing thread, no reseed.
    assert '不是问答白名单' in client.prompts[0]['general_question_instruction']
    assert '真人之间的交流仍忽略' in client.prompts[0]['general_question_instruction']
    assert client.prompts[0]['admission_context']['priority_sender_seqs'] == [1]
    assert client.prompts[0]['admission_context']['processor'] == 'codex'
    assert client.prompts[0]['admission_context']['group_description'] == '专门开发 ProjectGraph'


@pytest.mark.parametrize('domain', ['attendance', 'fanxiu', 'general'])
@pytest.mark.parametrize('awake', [False, True])
def test_image_waits_for_text_even_with_owner_priority_and_survives_restart(store, domain, awake):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, 'domain': domain, 'priority_sender_ids': [OWNER_ACCOUNT], 'followup_seconds': 0}
    if awake:
        store.update_hook('test', thread_id='existing', session_day=datetime.now(SHANGHAI).date().isoformat())
    photo = {**message(1, '<msg><img /></msg>', sender=OWNER_ACCOUNT), 'message_type': 3}
    ingest(store, photo)
    client = CandidateClient()
    assert not service(store).process_hook(hook, client)
    assert client.prompts == []
    assert store.hook('test')['consumed_seq'] == 0
    ingest(store, message(2, '这是今天拍的风景，请说明图中植物', sender=OWNER_ACCOUNT))
    resumed = service(AgentStore(store.path), sender=lambda *a, **k: {})
    assert resumed.process_hook(hook, client)
    submitted = client.prompts[0]['new_messages']
    assert [m['seq'] for m in submitted] == [1, 2]
    assert '等待' in submitted[0]['image_instruction']


def test_private_photo_is_retained_without_model_or_reply(store):
    svc, hook = private_service(store)
    photo = {**owner_event(1, '<msg><img /></msg>'), 'message_type': 3}
    ingest(store, photo)
    client = CandidateClient()
    assert not svc.process_hook(hook, client)
    assert client.prompts == []
    assert store.hook('owner')['consumed_seq'] == 0
    from backend.core.messaging.wechat_agent import owner_message_text
    assert '待解释附件' in owner_message_text(store.pending_messages('owner'), hook['account_id'], hook['chat_id'])


def service(store, source=None, sender=None):
    return WechatAgentService({"accounts": [ATTENDANCE_ACCOUNT], "hooks": [HOOK]}, store=store,
                              source_factory=lambda _: source or Source(store), sender=sender)


def test_pre_send_refresh_revises_unsent_answer_for_soft_followup(store):
    ingest(store, message(1, "@考勤返款 请查"))
    source = Source(store, lambda: ingest(store, message(2, "不用查，名单发错了")))
    sent = []
    svc = service(store, source, lambda *a, **kw: sent.append(a) or {"ok": True})
    client = CandidateClient()
    assert svc.process_hook(HOOK, client)
    assert len(client.prompts) == 2
    assert client.prompts[1]["previous_reply_unsent"] is True
    assert "previous_unsent_candidate" not in client.prompts[1]
    assert "context" not in client.prompts[1]
    assert [m["seq"] for m in client.prompts[1]["new_messages"]] == [2]
    assert sent == [(HOOK["chat_id"], "已纳入补充")]


def test_new_at_is_strong_but_normal_chat_is_soft(store):
    ingest(store, message(1, "@考勤返款 初始问题"))
    def during(hard_interrupt):
        ingest(store, message(2, "普通补充"))
        assert not hard_interrupt()
        ingest(store, message(3, "@考勤返款 请停止"))
        assert hard_interrupt()
    svc = service(store, sender=lambda *a, **kw: {"ok": True})
    client = CandidateClient(during)
    assert svc.process_hook(HOOK, client)
    assert len(client.prompts) == 2


def test_unaddressed_chat_does_not_wake_idle_agent(store):
    ingest(store, message(1, "今天吃什么"))
    client = CandidateClient()
    assert not service(store).process_hook(HOOK, client)
    assert client.prompts == []


class PlainClient:
    def __init__(self, text="直接回复"):
        self.text, self.prompts, self.opened = text, [], []
    def open_thread(self, thread_id, title, **kwargs):
        self.opened.append((thread_id, title, kwargs))
        return thread_id or f"private-{len(self.opened)}"
    def background(self, thread_id):
        return "旧会话中的未结问题，仅供参考"
    def compress_background(self, thread_id, previous_summary, stop_event, **kwargs):
        return "压缩交接：旧会话中的未结问题，仅供参考"
    def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
        assert kwargs["raw_text"] is True
        self.prompts.append(prompt)
        on_start("private-turn")
        return {"action": "reply", "text": self.text, "summary": self.text, "question_seqs": []}


def test_partial_answers_survive_restart_and_finish_in_reverse_order(store):
    sent=[]
    svc,hook=private_service(store,lambda _,text,**kw:sent.append(text) or {})
    ingest(store,owner_event(1,'长任务'),owner_event(2,'短问题'))
    assert svc.process_hook(hook,PlainClient('CODECLAW_REPLY_TO: 2\n短问题已完成'))
    assert store.hook('owner')['consumed_seq']==0
    assert [m['seq'] for m in store.pending_messages('owner')]==[1]
    # Reopen/recover through the public store API; no replay of delivered seq 2.
    recovered=AgentStore(store.path)
    recovered.recover()
    svc2,hook2=private_service(recovered,lambda _,text,**kw:sent.append(text) or {})
    assert svc2.process_hook(hook2,PlainClient('CODECLAW_REPLY_TO: 1\n长任务已完成'))
    assert recovered.hook('owner')['consumed_seq']==2
    assert recovered.pending_messages('owner')==[]
    assert len(sent)==2 and '短问题' in sent[0] and '长任务' in sent[1]


def test_configured_reply_limit_applies_to_quote_and_all_segments(store):
    sent=[]
    svc,hook=private_service(store,lambda _,text,**kw:sent.append(text) or {})
    svc.config['reply_max_chars']=100
    ingest(store,owner_event(1,'这是很长的原问题'*20))
    assert svc.process_hook(hook,PlainClient('CODECLAW_REPLY_TO: 1\n'+'正文'*150))
    assert all(0<len(part)<=100 for part in sent)
    assert ''.join(sent).endswith('正文'*150)


def test_watchdog_tracks_activity_instead_of_absolute_turn_duration(monkeypatch):
    from backend.core.codex import wechat_agent as transport
    client=transport.CodexWechatClient()
    clock=[0.0]
    monkeypatch.setattr(transport.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(client,'current_defaults',lambda:{'model':'default'})
    monkeypatch.setattr(client,'rpc',lambda *a,**kw:{'turn':{'id':'t'}})
    events=[
        {'method':'item/started','params':{'threadId':'daily','turnId':'t'}},
        {'method':'item/completed','params':{'threadId':'daily','turnId':'t','item':{'type':'agentMessage','text':'完成'}}},
        {'method':'turn/completed','params':{'threadId':'daily','turn':{'id':'t','status':'completed'}}}]
    class Events:
        def get(self,timeout):
            clock[0]+=8
            return events.pop(0)
    client.events=Events()
    result=client.run('daily','任务',lambda:False,threading.Event(),lambda _:None,timeout=10,raw_text=True)
    assert result['text']=='完成' and clock[0]==24


def test_silent_turn_still_interrupts_when_watchdog_expires(monkeypatch):
    import queue
    from backend.core.codex import wechat_agent as transport
    client=transport.CodexWechatClient()
    clock,calls=[0.0],[]
    monkeypatch.setattr(transport.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(client,'current_defaults',lambda:{'model':'default'})
    def rpc(method,params):
        calls.append(method)
        return {'turn':{'id':'t'}}
    monkeypatch.setattr(client,'rpc',rpc)
    class Events:
        def get(self,timeout):
            clock[0]+=11
            raise queue.Empty
    client.events=Events()
    with pytest.raises(TimeoutError,match='inactive'):
        client.run('daily','任务',lambda:False,threading.Event(),lambda _:None,timeout=10)
    assert calls==['turn/start','turn/interrupt']


def test_transport_activity_and_final_ignore_late_other_turn_events(monkeypatch):
    from backend.core.codex import wechat_agent as transport
    client, activity = transport.CodexWechatClient(), []
    monkeypatch.setattr(client, 'current_defaults', lambda: {'model': 'default'})
    def start(*args, **kwargs):
        notifications = [{'method': 'turn/started', 'params': {'threadId': 'daily',
            'turn': {'id': 'current', 'status': 'inProgress'}}}]
        for turn_id, body in [('old', 'stale'), ('current', 'current result')]:
            notifications.append({'method': 'item/completed', 'params': {'threadId': 'daily',
                'turnId': turn_id, 'item': {'id': turn_id, 'type': 'agentMessage', 'text': body}}})
        notifications.extend([
            {'method': 'turn/completed', 'params': {'threadId': 'daily',
                'turn': {'id': 'current', 'status': 'completed'}}},
            {'method': 'turn/started', 'params': {'threadId': 'daily',
                'turn': {'id': 'goal', 'status': 'inProgress'}}}])
        # The reader can already receive the Goal before run drains the old reply.
        for event in notifications:
            client.observe_activity(event)
            client.events.put(event)
        return {'turn': {'id': 'current'}}
    monkeypatch.setattr(client, 'rpc', start)
    result = client.run('daily', 'task', lambda: False, threading.Event(), lambda _: None,
                        raw_text=True, on_activity=activity.append)
    assert result['text'] == 'current result'
    assert len(activity) == 4
    assert all(row['turn_id'] != 'old' for row in activity)
    assert activity[-1]['turn_id'] == 'goal' and activity[-1]['state'] == 'inProgress'
    # The inbox reply is done, but a Goal continuation can still own the model.
    client.observe_activity({'method': 'turn/started', 'params': {'threadId': 'daily',
        'turn': {'id': 'goal', 'status': 'inProgress'}}})
    client.observe_activity({'method': 'turn/completed', 'params': {'threadId': 'daily',
        'turn': {'id': 'current', 'status': 'completed'}}})
    assert activity[-1]['turn_id'] == 'goal' and activity[-1]['state'] == 'inProgress'
    client.observe_activity({'method': 'turn/completed', 'params': {'threadId': 'daily',
        'turn': {'id': 'goal', 'status': 'completed'}}})
    assert activity[-1]['turn_id'] == 'goal' and activity[-1]['state'] == 'completed'


@pytest.mark.parametrize('completed', [False, True])
def test_transport_eof_marks_live_goal_lost_and_preserves_completed_turn(completed):
    from types import SimpleNamespace
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, records = CodexWechatClient(), []
    client.activity_observers['daily'] = records.append
    client.observe_activity({'method': 'turn/started', 'params': {'threadId': 'daily',
        'turn': {'id': 'goal', 'status': 'inProgress'}}})
    if completed:
        client.observe_activity({'method': 'turn/completed', 'params': {'threadId': 'daily',
            'turn': {'id': 'goal', 'status': 'completed'}}})
    client.process = SimpleNamespace(stdout=[])
    client._read()
    assert records[-1]['state'] == ('completed' if completed else 'transportLost')
    assert len(records) == 2
    client.observe_activity({'method': 'item/completed', 'params': {'threadId': 'daily',
        'turnId': 'goal', 'item': {'id': 'late', 'type': 'agentMessage'}}})
    assert len(records) == 2


@pytest.mark.parametrize('background_goal', [False, True])
def test_idle_reload_drains_before_restarting_and_restores_admission(store,monkeypatch,background_goal):
    from backend.core.messaging import wechat_agent as agent
    clock,stopped,started=[0.0],[],[]
    monkeypatch.setattr(agent,'agent_root',lambda:store.path.parent)
    monkeypatch.setattr(agent.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(agent.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def status():
        return {'running':True,'external_worker':{'pid':123},
                'hooks':[{**store.hook('test'),'configured':True, 'thread_id':'daily',
                          'status':'running' if clock[0]<2 else 'idle',
                          'turn_activity':{'worker_pid':123,'thread_id':'daily',
                            'state':'inProgress' if background_goal and clock[0]<4 else 'completed'}}]}
    monkeypatch.setattr(agent,'wechat_agent_status',status)
    monkeypatch.setattr(agent,'stop_wechat_agent_service',lambda:stopped.append(clock[0]))
    monkeypatch.setattr(agent,'start_wechat_agent_service',lambda:started.append(clock[0]) or {'running':True})
    assert agent.reload_wechat_agent_service_when_idle(timeout=10)['running']
    expected = 9.0 if background_goal else 7.0
    assert stopped==[expected] and started==[expected]
    assert store.hook('test')['retry_at']==0
    assert store.get_meta('reload')['status']=='complete'


def test_busy_reload_timeout_does_not_stop_worker(store,monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    clock,stopped=[0.0],[]
    monkeypatch.setattr(agent,'agent_root',lambda:store.path.parent)
    monkeypatch.setattr(agent.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(agent.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(agent,'wechat_agent_status',lambda:{'hooks':[{**store.hook('test'),'configured':True,'status':'running'}]})
    monkeypatch.setattr(agent,'stop_wechat_agent_service',lambda:stopped.append(True))
    with pytest.raises(TimeoutError,match='did not drain'):
        agent.reload_wechat_agent_service_when_idle(timeout=2)
    assert not stopped and store.hook('test')['retry_at']==0


def test_reload_request_is_deduplicated_and_uses_background_launcher(store,monkeypatch):
    import psutil
    from backend.core.messaging import wechat_agent as agent
    from backend.core.services import launcher
    from backend.core import temp_paths
    launched=[]
    monkeypatch.setattr(agent,'agent_root',lambda:store.path.parent)
    monkeypatch.setattr(temp_paths,'codeyun_temp_root',lambda *args:store.path.parent)
    monkeypatch.setattr(psutil,'pid_exists',lambda pid:pid==12345)
    class Process:
        pid=12345
    monkeypatch.setattr(launcher,'popen_service',lambda command,**kw:launched.append(command) or Process())
    result=agent.request_wechat_agent_reload()
    assert result['status']=='scheduled'
    assert agent.request_wechat_agent_reload()==result
    assert len(launched)==1 and launched[0][-1]=='reload-when-idle'


def private_service(store, sender=None, source=None):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, "key": "owner", "chat_id": OWNER_ACCOUNT, "kind": "owner_private"}
    svc = WechatAgentService({"accounts": [ATTENDANCE_ACCOUNT], "hooks": [hook]}, store=store,
                             source_factory=lambda _: source or Source(store), sender=sender or (lambda *a, **kw: {}))
    return svc, hook


def owner_event(seq, text, **kwargs):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    return {**message(seq, text, sender=OWNER_ACCOUNT), "chat_id": OWNER_ACCOUNT, **kwargs}


def test_owner_private_is_plain_text_no_at_and_long_reply_is_preserved(store):
    from backend.core.messaging.wechat_agent import split_wechat_text
    sent = []
    svc, hook = private_service(store, lambda _, text, **kw: sent.append(text) or {})
    text = "第一件事\n第二件事"
    ingest(store, owner_event(1, text))
    reply = "回复\n" + "x" * 1601
    client = PlainClient(reply)
    assert svc.process_hook(hook, client)
    assert text in client.prompts[0] and not client.prompts[0].startswith('{"task"')
    assert "".join(sent) == reply and all(len(part) <= 800 for part in sent)
    assert len(sent) == len(split_wechat_text(reply))
    assert not svc.process_hook(hook, client)


def test_owner_independent_questions_are_sent_as_linked_groups(store):
    sent=[]
    svc, hook=private_service(store, lambda _,text,**kw: sent.append(text) or {})
    ingest(store, owner_event(1,'床垫角标有什么用？',local_id=11),
           owner_event(2,'远控支持多标签',local_id=12), owner_event(3,'标签可关闭',local_id=13))
    client=PlainClient('CODECLAW_REPLY_TO: 1\n看起来是品牌角标。\n\n'
                       'CODECLAW_REPLY_TO: 2,3\n多标签已做好。')
    assert svc.process_hook(hook,client)
    assert sent==['回复「床垫角标有什么用？」\n\n看起来是品牌角标。',
                  '回复「远控支持多标签」\n\n多标签已做好。']
    receipt=store.deliveries('owner')[0]
    assert receipt['text']=='\n\n'.join(sent)
    assert 'CODECLAW_REPLY_TO' not in receipt['text']
    assert [group['question_seqs'] for group in receipt['reply_groups']]==[[1],[2,3]]
    assert all(group['quote_mode']=='text' for group in receipt['reply_groups'])
    assert not svc.process_hook(hook,client)


@pytest.mark.parametrize('output',[
    'CODECLAW_REPLY_TO: 999\n答复',
    'CODECLAW_REPLY_TO: 1\n第一组\nCODECLAW_REPLY_TO: 1\n第二组',
    'CODECLAW_REPLY_TO: 1\n第一组\nCODECLAW_REPLY_TO: 2\n',
])
def test_group_references_are_validated_before_any_send(store,output):
    sent=[]
    svc,hook=private_service(store,lambda *a,**kw: sent.append(a) or {})
    ingest(store,owner_event(1,'问题1'),owner_event(2,'问题2'))
    with pytest.raises(ValueError):
        svc.process_hook(hook,PlainClient(output))
    assert not sent and not store.status()['outbox']


def test_group_partial_delivery_cannot_replay_completed_group(store):
    sent=[]
    def sender(_,text,**kwargs):
        if sent:
            raise TimeoutError('第二组送达不明')
        sent.append(text)
        return {}
    svc,hook=private_service(store,sender)
    ingest(store,owner_event(1,'问题1'),owner_event(2,'问题2'))
    client=PlainClient('CODECLAW_REPLY_TO: 1\n答案1\nCODECLAW_REPLY_TO: 2\n答案2')
    assert not svc.process_hook(hook,client)
    assert store.hook('owner')['status']=='uncertain'
    assert not svc.process_hook(hook,client)
    assert sent==['回复「问题1」\n\n答案1']


def test_partial_reply_recovery_admits_new_questions_without_replaying_old_send(store, monkeypatch):
    from pyxllib.autogui import wechat_accounts
    from backend.core.messaging.wechat_reply_groups import reply_groups, quoted_group_text
    sent=[]
    svc, hook=private_service(store, lambda _, text, **kw: sent.append(text) or {})
    ingest(store, owner_event(1, '问题1'), owner_event(2, '问题2'))
    draft='CODECLAW_REPLY_TO: 1\n答案1\nCODECLAW_REPLY_TO: 2\n答案2'
    reply_id=store.reserve_reply('owner',2,draft)
    groups=reply_groups(draft,store.messages('owner'))
    # Production reserves the fully rendered text, not the model's raw body.
    # Keep a legacy long quote to verify reconciliation survives policy changes.
    groups[0]['quote_text']='问题1'+'旧'*25
    for group in groups:
        group['text']=quoted_group_text(group,group['text'])
    store.set_meta(f'reply_groups:{reply_id}',groups)
    store.set_meta('context_delivery:owner', {'thread_id':'old','submitted_seq':2,
                   'seeded':True,'receipts':{},'candidate':{'action':'reply','text':draft}})
    store.update_hook('owner',thread_id='old',session_day=datetime.now(SHANGHAI).date().isoformat())
    store.finish_reply(reply_id,'uncertain',{'error':'interrupted'})
    created=store.status()['outbox'][0]['created_at']
    monkeypatch.setattr('backend.core.messaging.wechat_agent.time.time', lambda: created+60)
    class Archive:
        def sync_from_live(self, **kw): pass
        def list_messages(self,*args,**kw):
            return {'items':[{'sender_username':ATTENDANCE_ACCOUNT,'server_id':123,'local_id':9,
                                  'create_time':created+2,'message_text':groups[0]['text']}]}
    monkeypatch.setattr(wechat_accounts,'get_account_storage',lambda _:Archive())
    ingest(store,owner_event(3,'新问题'))
    assert svc.recover_delivery_for_new_questions(hook)
    assert store.hook('owner')['consumed_seq']==1
    assert store.get_meta('context_delivery:owner')['candidate'] is None
    old=store.status()['outbox'][0]
    assert old['status']=='superseded_uncertain'
    assert store.deliveries('owner')[0]['result']['confirmed_groups']==[0]
    store.recover()
    assert store.hook('owner')['status']=='idle'
    assert store.reserve_reply('owner',2,draft) is None
    assert svc.process_hook(hook,PlainClient('CODECLAW_REPLY_TO: 2,3\n新答复'))
    assert sent==['回复「问题2」\n\n新答复']


def test_retiring_reply_requires_later_input_and_preserves_unverified_work(store):
    svc, hook=private_service(store)
    ingest(store,owner_event(1,'问题1'))
    reply_id=store.reserve_reply('owner',1,'答案')
    store.set_meta(f'reply_groups:{reply_id}',[{'question_seqs':[1]}])
    store.finish_reply(reply_id,'uncertain',{'error':'interrupted'})
    with pytest.raises(ValueError,match='later'):
        store.retire_uncertain_reply(reply_id,confirmed_groups=[],evidence='Checked archive')
    ingest(store,owner_event(2,'新问题'))
    with pytest.raises(ValueError,match='Invalid'):
        store.retire_uncertain_reply(reply_id,confirmed_groups=[9],evidence='Checked archive')
    store.retire_uncertain_reply(reply_id,confirmed_groups=[],evidence='No positive delivery evidence; frozen')
    assert store.hook('owner')['consumed_seq']==0
    assert [m['seq'] for m in store.messages('owner')]==[1,2]


def test_frozen_reply_and_unexplained_photo_do_not_relaunch_agent(store):
    svc, hook = private_service(store)
    ingest(store, owner_event(1, '导出并发送文件'))
    reply_id = store.reserve_reply('owner', 1, '文件交付')
    store.finish_reply(reply_id, 'uncertain', {'error': 'delivery unknown'})
    ingest(store, owner_event(2, '新问题'))
    store.retire_uncertain_reply(reply_id, confirmed_groups=[], evidence='Frozen; no replay')
    new_reply = store.reserve_reply('owner', 2, '新问题答复')
    store.set_meta(f'reply_groups:{new_reply}', [{'question_seqs': [2]}])
    store.finish_reply(new_reply, 'sent', {})
    ingest(store, owner_event(3, '<msg><img /></msg>', message_type=3))
    client = PlainClient()
    assert not svc.process_hook(hook, client)
    assert client.prompts == []
    assert [m['seq'] for m in store.pending_messages('owner')] == [1, 3]
    assert store.deliveries('owner')[1]['status'] == 'superseded_uncertain'
    # A later human message can still discuss the retained task, with a fresh identity.
    ingest(store, owner_event(4, '文件没有收到，怎么处理？'))
    assert svc.process_hook(hook, PlainClient('CODECLAW_REPLY_TO: 4\n投递仍需核对'))
    assert not store.question_answered('owner', 1)


def test_frozen_reply_accepts_late_delivery_confirmation_without_allowing_replay(store):
    svc, hook = private_service(store)
    ingest(store, owner_event(1, '第一件事'))
    reply_id = store.reserve_reply('owner', 1, '已完成')
    store.set_meta(f'reply_groups:{reply_id}', [{'question_seqs': [1]}])
    store.finish_reply(reply_id, 'uncertain', {'error': 'missing echo'})
    ingest(store, owner_event(2, '另一件事'))
    store.retire_uncertain_reply(reply_id, confirmed_groups=[], evidence='Not yet verified')
    with pytest.raises(ValueError, match='not uncertain'):
        store.reconcile_reply(reply_id, sent=False, evidence='Still no echo')
    assert store.reserve_reply('owner', 1, '重复') is None
    store.reconcile_reply(reply_id, sent=True, evidence='Exact archived outgoing message, local_id=262')
    assert store.status()['outbox'][0]['status'] == 'sent'
    assert store.hook('owner')['consumed_seq'] == 1
    assert next(h for h in store.status()['hooks'] if h['key'] == 'owner')['pending_question_seqs'] == [2]
    assert store.reserve_reply('owner', 1, '重复') is None


def test_owner_private_rollover_passes_old_id_and_background_without_routing_turn(store):
    svc, hook = private_service(store)
    store.update_hook("owner", thread_id="yesterday", session_day="2026-10-01",
                      last_question_at=datetime(2026, 10, 1, 20, tzinfo=SHANGHAI).timestamp())
    ingest(store, owner_event(1, "继续第一件事", timestamp=datetime(2026, 10, 2, 8, tzinfo=SHANGHAI).timestamp()))
    client = PlainClient()
    assert svc.process_hook(hook, client)
    assert len(client.prompts) == 1
    assert "yesterday" in client.prompts[0] and "未结问题" in client.prompts[0]
    assert store.hook("owner")["thread_id"] != "yesterday"


@pytest.mark.parametrize('domain', ['attendance', 'fanxiu', 'general'])
def test_rollover_injects_compressed_handoff_for_every_group_domain(store, domain):
    ingest(store, message(1, '@考勤返款 继续昨天的问题'))
    store.update_hook('test', thread_id='yesterday', session_day='2000-01-01',
                      last_question_at=time.time()-7201, summary='最后回复不能代替交接')
    client = CandidateClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook({**HOOK, 'domain':domain}, client)
    assert client.prompts[1]['previous_summary'] == '压缩交接：未结问题及已核验结果'
    assert store.get_meta('session_handoff:test')['from_thread_id'] == 'yesterday'
    assert store.get_meta('previous_thread:test') == 'yesterday'


def test_handoff_failure_retains_old_day_and_retry_reuses_saved_summary(store):
    svc, hook = private_service(store)
    store.update_hook('owner', thread_id='yesterday', session_day='2000-01-01', last_question_at=1)
    ingest(store, owner_event(1, '继续'))
    class FailingClient(PlainClient):
        def compress_background(self, *args, **kwargs):
            raise RuntimeError('compression failed')
    with pytest.raises(RuntimeError, match='compression failed'):
        svc.process_hook(hook, FailingClient())
    assert store.hook('owner')['thread_id'] == 'yesterday'
    assert store.hook('owner')['session_day'] == '2000-01-01'
    assert [m['seq'] for m in store.pending_messages('owner')] == [1]
    summary = svc.prepare_session_handoff('owner', 'yesterday', '2026-10-07', PlainClient())
    reopened = AgentStore(store.path)
    svc.store = reopened
    assert svc.prepare_session_handoff('owner', 'yesterday', '2026-10-07', FailingClient()) == summary


def test_compression_contract_uses_summary_instead_of_last_reply(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    calls = []
    def run(thread_id, prompt, *args, **kwargs):
        calls.append((thread_id, json.loads(prompt)))
        return {'summary':'旧任务完成；第二项待办', 'text':'不应作为交接'}
    monkeypatch.setattr(client, 'run', run)
    assert client.compress_background('old', '此前约定', threading.Event()) == '旧任务完成；第二项待办'
    assert calls[0][0] == 'old'
    assert calls[0][1]['previous_handoff'] == '此前约定'
    monkeypatch.setattr(client, 'run', lambda *a, **k: {'summary':''})
    with pytest.raises(ValueError, match='old conversation retained'):
        client.compress_background('old', '', threading.Event())


def test_private_sender_identity_is_verified_instead_of_display_name(store):
    svc, hook = private_service(store)
    ingest(store, owner_event(1, "我是代号4101", sender_id="impostor"))
    client = PlainClient()
    assert not svc.process_hook(hook, client) and not client.prompts


def test_group_owner_message_wakes_without_at(store):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, "priority_sender_ids": [OWNER_ACCOUNT]}
    ingest(store, message(1, "请处理这个问题", sender=OWNER_ACCOUNT))
    client = CandidateClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(hook, client)
    assert len(client.prompts) == 1


def test_partial_segment_delivery_is_uncertain_and_never_replayed(store):
    sent = []
    def sender(_, text, **kwargs):
        if sent:
            raise TimeoutError("second part unknown")
        sent.append(text)
        return {}
    svc, hook = private_service(store, sender)
    ingest(store, owner_event(1, "请给长结果"))
    client = PlainClient("x" * 1600)
    assert not svc.process_hook(hook, client)
    assert store.hook("owner")["status"] == "uncertain"
    assert not svc.process_hook(hook, client) and len(sent) == 1
    reply = store.status()["outbox"][0]
    with pytest.raises(ValueError, match="部分消息"):
        store.reconcile_reply(reply["id"], sent=False, evidence="后面的分段没有送出")


def test_private_image_output_uses_pinned_host_sender_and_no_marker_text(store, tmp_path):
    from PIL import Image
    image = tmp_path / "reply.png"
    Image.new("RGB", (20, 10), "white").save(image)
    text_sent, images_sent = [], []
    svc, hook = private_service(store, lambda _, text, **kw: text_sent.append(text) or {})
    svc.image_sender = lambda recipient, path, **kwargs: images_sent.append((recipient, path, kwargs["sender_account_id"])) or {"verified": True}
    ingest(store, owner_event(1, "请发结果图片"))
    client = PlainClient(f"结果如下\nCODECLAW_IMAGE: {image}")
    assert svc.process_hook(hook, client)
    assert text_sent == ["结果如下"]
    assert images_sent == [(hook["chat_id"], image, ATTENDANCE_ACCOUNT)]
    assert not svc.process_hook(hook, client)


def test_delivery_failure_is_visible_and_blocks_reexecution(store):
    ingest(store, message(1, "@考勤返款 请查"))
    def fail(*args, **kwargs):
        raise TimeoutError("delivery unknown")
    svc = service(store, sender=fail)
    client = CandidateClient()
    assert not svc.process_hook(HOOK, client)
    assert store.hook("test")["status"] == "uncertain"
    assert not svc.process_hook(HOOK, client)
    assert len(client.prompts) == 1


def test_file_only_reply_uses_pinned_sender_without_text_or_replay(store, tmp_path):
    file = tmp_path / "report.txt"
    file.write_text("report", encoding="utf-8")
    texts, files = [], []
    svc, hook = private_service(store, lambda _, text, **kw: texts.append(text) or {})
    svc.file_sender = lambda recipient, path, **kw: files.append((recipient, path, kw["sender_account_id"])) or {"verified": True}
    ingest(store, owner_event(1, "发报告文件"))
    client = PlainClient(f"CODECLAW_FILE: {file}")
    assert svc.process_hook(hook, client)
    assert not texts and files == [(hook["chat_id"], file, ATTENDANCE_ACCOUNT)]
    assert not svc.process_hook(hook, client)


def test_wal_replay_only_includes_valid_committed_frames():
    import struct
    from pyxllib.autogui.wechat_updates import _checksum
    page_size = 4096
    header = struct.pack(">IIIIII", 0x377f0682, 3007000, page_size, 1, 123, 456)
    sums = _checksum(header, (0, 0), "<")
    wal = header + struct.pack(">II", *sums)
    for pgno, size in [(1, 1), (2, 0)]:
        frame = struct.pack(">IIII", pgno, size, 123, 456)
        page = bytes([pgno]) * page_size
        sums = _checksum(frame[:8] + page, sums, "<")
        wal += frame + struct.pack(">II", *sums) + page
    assert [frame[0] for frame in committed_wal_frames(wal)] == [1]
    assert [frame[0] for frame in committed_wal_frames(wal[:-100])] == [1]
    assert [frame[0] for frame in committed_wal_frames(wal[:-1] + b"x")] == [1]


@pytest.mark.parametrize("domain", ["attendance", "fanxiu", "general"])
def test_daily_group_context_is_seeded_once_and_restart_sends_only_delta(store, domain):
    hook = {**HOOK, "domain": domain}
    sent = []
    svc = service(store, sender=lambda *a, **k: sent.append(a) or {})
    first = message(1, "@考勤返款 abcd")
    first.update(source="<large raw transport XML>", source_db="message.db", server_id="unused")
    ingest(store, first)
    client = CandidateClient()
    assert svc.process_hook(hook, client)
    assert client.prompts[0]["context"] == []  # New messages occur only once.
    assert "source" not in client.prompts[0]["new_messages"][0]
    # New service object, same durable ledger and thread.
    resumed = service(AgentStore(store.path), sender=lambda *a, **k: sent.append(a) or {})
    ingest(store, message(2, "@考勤返款 efgh"))
    assert resumed.process_hook(hook, client)
    second = client.prompts[1]
    assert [m["seq"] for m in second["new_messages"]] == [2]
    assert "context" not in second and "previous_summary" not in second and "group_context" not in second
    assert [r["through_seq"] for r in second["delivery_receipts"]] == [1]
    ingest(store, message(3, "@考勤返款 ijkl"))
    assert resumed.process_hook(hook, client)
    assert [r["through_seq"] for r in client.prompts[2]["delivery_receipts"]] == [2]
    assert len(sent) == 3


def test_interrupt_and_restart_do_not_replay_admitted_question(store):
    ingest(store, message(1, "@考勤返款 abcd"))
    svc = service(store, sender=lambda *a, **k: {})
    class InterruptedClient(CandidateClient):
        def run(self, *args, **kwargs):
            super().run(*args, **kwargs)
            svc.stop_event.set()
            return {"interrupted": True}
    client = InterruptedClient()
    assert not svc.process_hook(HOOK, client)
    assert store.hook("test")["consumed_seq"] == 0
    assert store.get_meta("context_delivery:test")["submitted_seq"] == 1
    ingest(store, message(2, "@考勤返款 efgh"))
    retry = CandidateClient()
    assert service(AgentStore(store.path), sender=lambda *a, **k: {}).process_hook(HOOK, retry)
    assert [m["seq"] for m in retry.prompts[0]["new_messages"]] == [2]
    assert retry.prompts[0]["pending_question_seqs"] == [1]
    assert "context" not in retry.prompts[0]


@pytest.mark.parametrize("admitted", [False, True])
def test_lost_start_ack_is_reconciled_before_retry(store, admitted):
    ingest(store, message(1, "@考勤返款 abcd"))
    class LostAckClient(CandidateClient):
        def run(self, thread_id, prompt, *args, **kwargs):
            self.lost_prompt = prompt
            raise TimeoutError("lost acknowledgement")
    lost = LostAckClient()
    svc = service(store, sender=lambda *a, **k: {})
    with pytest.raises(TimeoutError):
        svc.process_hook(HOOK, lost)
    assert store.hook("test")["consumed_seq"] == 0
    class RetryClient(CandidateClient):
        def has_user_input(self, thread_id, prompt):
            assert prompt == lost.lost_prompt
            return admitted
    retry = RetryClient()
    assert service(AgentStore(store.path), sender=lambda *a, **k: {}).process_hook(HOOK, retry)
    assert [m["seq"] for m in retry.prompts[0]["new_messages"]] == ([] if admitted else [1])
    assert retry.prompts[0]["pending_question_seqs"] == ([1] if admitted else [])
    assert ("context" in retry.prompts[0]) is (not admitted)


def test_soft_steer_progress_survives_interrupt_and_restart(store):
    ingest(store, message(1, "@考勤返款 abcd"))
    svc = service(store, sender=lambda *a, **k: {})
    class SteeringClient(CandidateClient):
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
            on_start("turn")
            ingest(store, message(2, "efgh"))
            update = json.loads(kwargs["soft_updates"](False))
            assert [m["seq"] for m in update["soft_new_messages"]] == [2]
            kwargs["soft_updates"](True)
            stop_event.set()
            return {"interrupted": True}
    assert not svc.process_hook(HOOK, SteeringClient())
    ingest(store, message(3, "@考勤返款 ijkl"))
    retry = CandidateClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(HOOK, retry)
    assert [m["seq"] for m in retry.prompts[0]["new_messages"]] == [3]
    assert retry.prompts[0]["pending_question_seqs"] == [1, 2]


def test_new_daily_thread_gets_seed_even_with_previous_summary(store):
    ingest(store, message(1, "@考勤返款 新问题"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01",
                      summary="昨天未结事项", last_question_at=time.time() - 7201)
    client = CandidateClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(HOOK, client)
    assert "context" not in client.prompts[0]  # Route inside existing history.
    assert "context" in client.prompts[1] and "previous_summary" in client.prompts[1]
    assert [m["seq"] for m in client.prompts[1]["new_messages"]] == [1]


def test_skipped_chatter_is_added_once_when_later_question_wakes_thread(store):
    client, svc = CandidateClient(), service(store, sender=lambda *a, **k: {})
    ingest(store, message(1, "@考勤返款 首问"))
    assert svc.process_hook({**HOOK, "followup_seconds": 1}, client)
    store.update_hook("test", awake_until=0)
    ingest(store, message(2, "这是后续问题背景"))
    assert not svc.process_hook({**HOOK, "followup_seconds": 1}, client)
    ingest(store, message(3, "@考勤返款 处理刚才那件事"))
    assert svc.process_hook(HOOK, client)
    assert [m["seq"] for m in client.prompts[1]["new_messages"]] == [2, 3]


def test_xml_projection_preserves_quotes_links_and_attachment_identity():
    from backend.core.messaging.wechat_agent import model_message
    quoted = message(1, 'user:\n<msg><appmsg><title>@考勤 继续</title><type>57</type>'
                     '<refermsg><displayname>学员</displayname><content>第3课0%</content><svrid>123</svrid></refermsg>'
                     '</appmsg></msg>')
    result = model_message(quoted)
    assert result["text"] == "@考勤 继续"
    assert result["quote"] == {"displayname": "学员", "content": "第3课0%", "svrid": "123"}
    image = {**message(2, '<msg><img aeskey="secret" /></msg>'), "local_id": 17, "message_type": 3}
    projected = model_message(image)
    assert projected["local_id"] == 17 and projected["message_type"] == 3
    assert "aeskey" not in projected["text"]
    unknown = '<msg><unknown>业务原文</unknown></msg>'
    assert model_message(message(3, unknown))["text"] == unknown


def test_owner_private_restart_continues_admitted_input_without_repeating_text(store):
    svc, hook = private_service(store)
    ingest(store, owner_event(1, "abcd"))
    class FailedClient(PlainClient):
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
            on_start("turn")
            raise RuntimeError("model failed after admission")
    with pytest.raises(RuntimeError):
        svc.process_hook(hook, FailedClient())
    ingest(store, owner_event(2, "efgh"))
    retry = PlainClient()
    resumed, hook = private_service(AgentStore(store.path))
    assert resumed.process_hook(hook, retry)
    assert "abcd" not in retry.prompts[0] and "efgh" in retry.prompts[0]
    assert "已提交的消息 [1]" in retry.prompts[0]


def test_lost_steer_ack_is_reconciled_without_replaying_soft_message(store):
    ingest(store, message(1, "@考勤返款 abcd"))
    svc = service(store, sender=lambda *a, **k: {})
    class LostSteer(CandidateClient):
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
            on_start("turn")
            ingest(store, message(2, "efgh"))
            self.lost_prompt = kwargs["soft_updates"](False)
            raise TimeoutError("lost steer acknowledgement")
    lost = LostSteer()
    with pytest.raises(TimeoutError):
        svc.process_hook(HOOK, lost)
    class Retry(CandidateClient):
        def has_user_input(self, thread_id, prompt):
            assert prompt == lost.lost_prompt
            return True
    retry = Retry()
    assert service(store, sender=lambda *a, **k: {}).process_hook(HOOK, retry)
    assert retry.prompts[0]["new_messages"] == []
    assert retry.prompts[0]["pending_question_seqs"] == [1, 2]


def test_consumed_reply_candidate_is_not_reused_after_crash(store):
    ingest(store, message(1, "@考勤返款 首问"))
    client = CandidateClient()
    svc = service(store, sender=lambda *a, **k: {})
    assert svc.process_hook(HOOK, client)
    # Simulate crashing after sent receipt committed, before clearing candidate.
    progress = store.get_meta("context_delivery:test")
    progress["candidate"] = {"action": "reply", "text": "旧回答", "question_seqs": [1]}
    store.set_meta("context_delivery:test", progress)
    ingest(store, message(2, "@考勤返款 新问题"))
    assert svc.process_hook(HOOK, client)
    assert client.prompts[1]["previous_reply_unsent"] is False


def test_public_history_receipt_requires_exact_user_input(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client = CodexWechatClient()
    def rpc(method, params):
        assert method == "thread/read" and params == {"threadId": "daily", "includeTurns": True}
        return {"thread": {"turns": [{"items": [
            {"type": "agentMessage", "text": "not a receipt"},
            {"type": "userMessage", "content": [{"type": "text", "text": "abcd"}]}]}]}}
    monkeypatch.setattr(client, "rpc", rpc)
    assert client.has_user_input("daily", "abcd")
    assert not client.has_user_input("daily", "abc")


def test_rollover_does_not_replay_completed_inbox_as_new_work(store):
    ingest(store, message(1, "@考勤返款 昨天已处理"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01", consumed_seq=1,
                      summary="旧任务已完成", last_question_at=time.time() - 7201)
    ingest(store, message(2, "@考勤返款 今天的新问题"))
    class RoutingClient(CandidateClient):
        def run(self, *args, **kwargs):
            return {**super().run(*args, **kwargs), "question_seqs": [2]}
    client = RoutingClient()
    assert service(store, sender=lambda *a, **k: {}).process_hook(HOOK, client)
    assert [m["seq"] for m in client.prompts[1]["new_messages"]] == [2]
    assert [(m["seq"], m["history_only"]) for m in client.prompts[1]["context"]] == [(1, True)]


@pytest.mark.parametrize('escalate', [False, True])
def test_routine_diary_selects_luna_and_escalates_before_delivery(store, escalate):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, 'domain':'general', 'description':'所有者日记自动整理保存',
            'priority_sender_ids':[OWNER_ACCOUNT], 'routine_model':'gpt-6-luna', 'routine_effort':'low'}
    ingest(store, {**message(1, '到了国家图书馆', sender=OWNER_ACCOUNT), 'message_type':1})
    class RoutineClient(CandidateClient):
        def __init__(self):
            super().__init__()
            self.profiles = []
        def run(self, *args, **kwargs):
            self.profiles.append(kwargs.get('model_profile'))
            result = super().run(*args, **kwargs)
            if kwargs.get('model_profile'):
                return {**result, 'action':'defer' if escalate else 'reply',
                        'text':'' if escalate else '轻量记录完成', 'requires_default_model':escalate}
            return {**result, 'text':'默认模型复核完成'}
    client, sent = RoutineClient(), []
    assert service(store, sender=lambda *a, **kw: sent.append(a) or {}).process_hook(hook, client)
    assert client.profiles[0] == {'model':'gpt-6-luna','effort':'low'}
    assert len(client.profiles) == (2 if escalate else 1)
    if escalate:
        assert client.profiles[1] is None
        assert '避免重复保存' in client.prompts[1]['lightweight_attempt']['instruction']
    assert sent == [(hook['chat_id'], '默认模型复核完成' if escalate else '轻量记录完成')]


def test_complex_soft_followup_upgrades_routine_turn_before_send(store):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, 'domain':'general', 'description':'所有者日记自动整理保存',
            'priority_sender_ids':[OWNER_ACCOUNT], 'routine_model':'gpt-6-luna'}
    ingest(store, {**message(1, '到了国家图书馆', sender=OWNER_ACCOUNT), 'message_type':1})
    class SteeredClient(CandidateClient):
        def __init__(self):
            super().__init__()
            self.profiles = []
        def run(self, *args, **kwargs):
            self.profiles.append(kwargs.get('model_profile'))
            result = super().run(*args, **kwargs)
            if len(self.prompts) == 1:
                ingest(store, {**message(2, '请修复读取图片的接口',sender=OWNER_ACCOUNT),'message_type':1})
                assert kwargs['soft_updates'](False)
                kwargs['soft_updates'](True)
                return {**result, 'requires_default_model':False}
            return {**result,'text':'复杂补充已交默认模型','question_seqs':[1,2]}
    client, sent = SteeredClient(), []
    assert service(store, sender=lambda *a, **kw: sent.append(a) or {}).process_hook(hook, client)
    assert client.profiles == [{'model':'gpt-6-luna','effort':'low'},None]
    assert [m['seq'] for m in client.prompts[1]['new_messages']] == [1,2]
    assert sent == [(hook['chat_id'],'复杂补充已交默认模型')]


def test_routine_transport_failure_upgrades_once_before_sending(store):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    hook = {**HOOK, 'domain':'general', 'description':'所有者日记自动保存',
            'priority_sender_ids':[OWNER_ACCOUNT], 'routine_model':'gpt-6-luna'}
    ingest(store, {**message(1, '到了国家图书馆', sender=OWNER_ACCOUNT), 'message_type':1})
    class FailingClient(CandidateClient):
        def run(self, *args, **kwargs):
            if kwargs.get('model_profile'):
                raise RuntimeError('routine model unavailable')
            return super().run(*args, **kwargs)
    client, sent = FailingClient(), []
    assert service(store, sender=lambda *a, **kw: sent.append(a) or {}).process_hook(hook, client)
    assert len(client.prompts) == 1
    assert 'routine model unavailable' in client.prompts[0]['lightweight_attempt']['summary']
    assert sent == [(hook['chat_id'],'初稿')]


def test_engineering_diary_consumes_verified_record_without_model_or_send(store, monkeypatch):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.core.messaging import wechat_diary_recording as recording
    hook={**HOOK,'domain':'general','description':'所有者日记自动保存',
          'priority_sender_ids':[OWNER_ACCOUNT],'routine_model':'gpt-6-luna',
          'engineering_diary':True,'diary_silent':True}
    ingest(store,{**message(1,'到了国家图书馆',sender=OWNER_ACCOUNT),'message_type':1})
    monkeypatch.setattr(recording,'record_plain_diary',lambda *a,**k:
        {'action':'ignore','text':'','summary':'工程回读成功','question_seqs':[1]})
    client,sent=CandidateClient(),[]
    assert service(store,sender=lambda *a,**k:sent.append(a) or {}).process_hook(hook,client)
    assert not client.prompts and not sent
    assert store.hook('test')['consumed_seq']==1
    assert store.get_meta('model_route:test')['model']=='none'


def test_silent_diary_still_interprets_fallback_but_does_not_send_confirmation(store, monkeypatch):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.core.messaging import wechat_diary_recording as recording
    hook={**HOOK,'domain':'general','description':'所有者日记自动保存',
          'priority_sender_ids':[OWNER_ACCOUNT],'routine_model':'gpt-6-luna',
          'engineering_diary':True,'diary_silent':True}
    ingest(store,{**message(1,'到了国家图书馆',sender=OWNER_ACCOUNT),'message_type':1})
    monkeypatch.setattr(recording,'record_plain_diary',lambda *a,**k:None)
    class SilentClient(CandidateClient):
        def run(self,*a,**kw):
            return {**super().run(*a,**kw),'action':'ignore','text':'','summary':'保存回读成功'}
    client,sent=SilentClient(),[]
    assert service(store,sender=lambda *a,**k:sent.append(a) or {}).process_hook(hook,client)
    assert len(client.prompts)==1 and not sent
    assert client.prompts[0]['diary_silent'] is True
    assert store.hook('test')['consumed_seq']==1


def test_silent_diary_preserves_failure_reply(store, monkeypatch):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.core.messaging import wechat_diary_recording as recording
    hook={**HOOK,'domain':'general','description':'所有者日记自动保存',
          'priority_sender_ids':[OWNER_ACCOUNT],'routine_model':'gpt-6-luna',
          'engineering_diary':True,'diary_silent':True}
    ingest(store,{**message(1,'到了国家图书馆',sender=OWNER_ACCOUNT),'message_type':1})
    def failure(*a,**k): raise RuntimeError('readback failed after write')
    monkeypatch.setattr(recording,'record_plain_diary',failure)
    client,sent=CandidateClient(),[]
    assert service(store,sender=lambda *a,**k:sent.append(a) or {}).process_hook(hook,client)
    assert 'readback failed' in client.prompts[0]['engineering_recording_attempt']['error']
    assert sent==[(hook['chat_id'],'初稿')]


def test_config_compare_and_set_preserves_concurrent_changes(tmp_path, monkeypatch):
    import backend.core.messaging.wechat_agent as module
    monkeypatch.setattr(module,'agent_root',lambda:tmp_path)
    initial = module.save_config({'accounts':[ATTENDANCE_ACCOUNT],'hooks':[HOOK]})
    updated = {**initial,'reply_max_chars':500}
    module.save_config(updated, expected_config=initial)
    with pytest.raises(RuntimeError, match='其他操作更新'):
        module.save_config(initial, expected_config=initial)
    assert module.load_config()['reply_max_chars'] == 500


@pytest.mark.parametrize('active_turn,active_goal', [(True, False), (False, True)])
def test_writer_conflict_cannot_duplicate_active_business(monkeypatch, active_turn, active_goal):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, 'start', lambda: None)
    def rpc(method, params):
        calls.append(method)
        if method == 'thread/resume':
            raise RuntimeError('thread old already has an active writer')
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'notLoaded'}, 'turns': [{'status': 'inProgress' if active_turn else 'completed'}]}}
        if method == 'thread/goal/get':
            return {'goal': {'status': 'active' if active_goal else 'complete'}}
        raise AssertionError('must not fork or start another operator')
    monkeypatch.setattr(client, 'rpc', rpc)
    with pytest.raises(RuntimeError, match='Writer recovery refused'):
        client.open_thread('old', 'WeChat')
    assert 'thread/fork' not in calls


def test_supervisor_restores_only_its_previously_leased_thread(store, monkeypatch):
    from backend.core.messaging import wechat_agent as agent
    monkeypatch.setattr(agent, 'agent_root', lambda: store.path.parent)
    monkeypatch.setattr(agent, 'load_config', lambda: {'hooks': [HOOK]})
    store.update_hook('test', thread_id='daily')
    agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='manager', thread_id='daily')
    store.update_hook('test', thread_id='replacement')
    with pytest.raises(ValueError):
        agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='other', thread_id='daily', restore_replaced_thread=True)
    assert store.hook('test')['thread_id'] == 'replacement'
    lease = agent.set_wechat_agent_execution_owner('test', supervisor_thread_id='manager', thread_id='daily', restore_replaced_thread=True)
    assert lease['thread_id'] == store.hook('test')['thread_id'] == 'daily'
    assert store.hook('test')['consumed_seq'] == 0


def test_schema_recovery_preserves_active_goal_between_turns(monkeypatch):
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, 'start', lambda: None)
    def rpc(method, params):
        calls.append(method)
        if method == 'thread/read':
            return {'thread': {'status': {'type': 'notLoaded'}, 'turns': [{'status': 'completed'}]}}
        if method == 'thread/goal/get':
            return {'goal': {'status': 'active'}}
        raise AssertionError('must preserve the unfinished Goal host')
    monkeypatch.setattr(client, 'rpc', rpc)
    assert client.open_thread('old', 'WeChat', recover_output_schema=True) == 'old'
    assert calls == ['thread/goal/get']


def test_schema_mismatch_retries_same_goal_host_without_schema(monkeypatch):
    import threading
    from backend.core.codex.wechat_agent import CodexWechatClient
    client, calls = CodexWechatClient(), []
    monkeypatch.setattr(client, 'current_defaults', lambda: {})
    def rpc(method, params):
        calls.append((method, params))
        if method == 'thread/goal/get':
            return {'goal': {'status': 'active'}}
        if len(calls) == 1:
            raise RuntimeError('ActiveTurnOutputSchemaMismatch')
        assert method == 'turn/start'
        return {'turn': {'id': 'accepted'}}
    monkeypatch.setattr(client, 'rpc', rpc)
    client.events.put({'method': 'turn/completed', 'params': {
        'threadId': 'old', 'turn': {'id': 'accepted', 'status': 'interrupted'}}})
    result = client.run('old', 'inbox', lambda: False, threading.Event(), lambda _: None)
    assert result == {'interrupted': True}
    assert [m for m, _ in calls] == ['turn/start', 'thread/goal/get', 'turn/start']
    assert calls[0][1]['threadId'] == calls[2][1]['threadId'] == 'old'
    assert 'outputSchema' not in calls[2][1]
