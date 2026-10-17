"""Only official DS owns raw context; every CC delegation is fresh and translated."""
import copy
import json
from types import SimpleNamespace
import threading

import pytest

from backend.core.ai import privacy, steward


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(privacy, "_root", lambda: tmp_path / "ai" / "privacy")
    def extract(**kwargs):
        assert kwargs["provider_id"] == "ds"
        text = kwargs["messages"][0]["content"]
        return {"provider": "deepseek-official", "content": json.dumps({"entities": [
            {"value": value, "kind": kind} for value, kind in
            [("original-secret", "PASSWORD"), ("李小明", "PERSON"), ("new-result-secret", "TOKEN")]
            if value in text]})}
    monkeypatch.setattr(privacy, "chat_with_provider", extract)


def reply(content):
    return {"provider": "deepseek-official", "content": content}


def delegate(task, call_id="call-1"):
    call = {"id": call_id, "type": "function", "function": {
        "name": "delegate_cc", "arguments": json.dumps({"task": task}, ensure_ascii=False)}}
    return {"provider": "deepseek-official", "content": "", "tool_calls": [call],
            "message": {"role": "assistant", "content": "", "tool_calls": [call]}}


def test_general_chat_stays_with_ds(setup, monkeypatch):
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: reply("你好，我可以帮你。"))
    monkeypatch.setattr(steward, "run_agent", lambda **kw: pytest.fail("Chat must not delegate"))
    state = {}
    answer = steward.respond(state, "你好", request_id="1", checkpoint=lambda _: None, allow_execution=True)
    assert answer == "你好，我可以帮你。"
    assert steward.respond(state, "你好", request_id="1", checkpoint=lambda _: None, allow_execution=True) == answer


def test_old_raw_context_quotes_and_results_never_enter_cc(setup, monkeypatch):
    state = {}
    calls = []
    responses = iter([reply("记住了。"), delegate("为李小明使用original-secret执行登录"), reply("已经完成。"),
                      delegate("使用new-result-secret查询结果", "call-2"), reply("查询完成。")])
    def ds(**kwargs):
        assert kwargs["provider_id"] == "ds"
        calls.append(copy.deepcopy(kwargs["messages"]))
        return next(responses)
    monkeypatch.setattr(steward, "chat_with_provider", ds)
    delegated = []
    def cc(**kwargs):
        delegated.append(kwargs)
        assert kwargs["resource"] == "cc"
        assert "session_id" not in kwargs and "session_provider" not in kwargs
        for private in ["李小明", "original-secret", "new-result-secret", "DS_ONLY_HISTORY"]:
            assert private not in kwargs["prompt"]
        return {"ok": True, "text": "执行成功，结果new-result-secret", "session_id": "never-resume-this"}
    monkeypatch.setattr(steward, "run_agent", cc)
    persist = lambda _: None
    steward.respond(state, 'DS_ONLY_HISTORY 引用姓名李小明，密码original-secret', request_id="1", checkpoint=persist, allow_execution=True)
    steward.respond(state, "帮我登录", request_id="2", checkpoint=persist, allow_execution=True)
    steward.respond(state, "继续查结果", request_id="3", checkpoint=persist, allow_execution=True)
    assert len(delegated) == 2
    assert "original-secret" in json.dumps(calls, ensure_ascii=False)  # trusted DS keeps full context
    assert "PASSWORD_0001" in delegated[0]["prompt"]
    assert "TOKEN_0001" in delegated[1]["prompt"]


def test_cc_crash_does_not_replay_operation(setup, monkeypatch):
    responses = iter([delegate("执行一次操作"), reply("上次操作结果需要先核验。")])
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: next(responses))
    counter = []
    def crash(**kwargs):
        counter.append(kwargs)
        raise RuntimeError("original-secret must not enter logged errors")
    monkeypatch.setattr(steward, "run_agent", crash)
    state = {}
    with pytest.raises(steward.StewardError, match="cc_execution_needs_reconciliation") as exc:
        steward.respond(state, "执行", request_id="1", checkpoint=lambda _: None, allow_execution=True)
    assert "original-secret" not in str(exc.value)
    assert steward.respond(state, "执行", request_id="1", checkpoint=lambda _: None,
                           allow_execution=True) == "上次操作结果需要先核验。"
    assert len(counter) == 1


def test_same_delegation_is_not_executed_twice(setup, monkeypatch):
    responses = iter([delegate("执行一次"), delegate("执行一次", "call-2"), reply("完成")])
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: next(responses))
    counter = []
    monkeypatch.setattr(steward, "run_agent", lambda **kw: counter.append(kw) or {"ok": True, "text": "完成"})
    steward.respond({}, "执行", request_id="1", checkpoint=lambda _: None, allow_execution=True)
    assert len(counter) == 1


def test_model_cannot_grant_execution_authority(setup, monkeypatch):
    responses = iter([delegate("执行不获授权的操作"), reply("需要所有者授权。")])
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: next(responses))
    monkeypatch.setattr(steward, "run_agent", lambda **kw: pytest.fail("No execution authority"))
    assert steward.respond({}, "执行", request_id="1", checkpoint=lambda _: None, allow_execution=False) == "需要所有者授权。"


def test_wrong_ds_provider_is_rejected(setup, monkeypatch):
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: {"provider": "opencode-go", "content": "bad"})
    with pytest.raises(steward.StewardError, match="requires_official"):
        steward.respond({}, "你好", request_id="1", checkpoint=lambda _: None, allow_execution=True)


def test_ds_processor_constructs_no_codex_or_claude_client(monkeypatch):
    from backend.core.messaging.wechat_agent import WechatAgentService
    from backend.core.messaging.wechat_ds import DsWechatClient
    service = object.__new__(WechatAgentService)
    service.client_factory = lambda: pytest.fail("No Codex client")
    service.claude_client_factory = lambda: pytest.fail("No Claude conversation client")
    assert isinstance(service.new_client({"processor": "ds", "domain": "general"}), DsWechatClient)


def test_wechat_ds_uses_local_admission_and_guarded_delivery(setup, monkeypatch, tmp_path):
    from backend.core.messaging import wechat_ds
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.tests.test_wechat_agent import ingest, message, HOOK
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path / "events.sqlite")
    store.ensure_hook(**{k: HOOK[k] for k in ("key", "account_id", "chat_id")})
    ingest(store, message(1, "你好", sender=OWNER_ACCOUNT))
    sent = []
    service = SimpleNamespace(store=store, config={}, workers=[], last_error=None,
        stop_event=threading.Event(), poll_once=lambda: None,
        sender=lambda *args, **kwargs: sent.append(args) or {})
    def intake(**kwargs):
        assert kwargs["provider_id"] == "ollama"
        return {"content": '{"respond":true}'}
    monkeypatch.setattr(wechat_ds, "chat_with_provider", intake)
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: reply("你好，可以聊天，也可以帮你办事。"))
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    assert len(sent) == 1
    assert store.hook("test")["consumed_seq"] == 1
    assert store.hook("test")["thread_id"].startswith("ds:")
    assert not wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})


def test_wechat_restart_reuses_saved_answer_instead_of_reexecuting(setup, monkeypatch, tmp_path):
    from backend.core.messaging import wechat_ds
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.tests.test_wechat_agent import ingest, message, HOOK
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path / "events.sqlite")
    store.ensure_hook(**{k: HOOK[k] for k in ("key", "account_id", "chat_id")})
    ingest(store, message(1, "执行任务", sender=OWNER_ACCOUNT))
    service = SimpleNamespace(store=store, config={}, workers=[], last_error=None,
        stop_event=threading.Event(), poll_once=lambda: None, sender=lambda *args, **kwargs: {})
    monkeypatch.setattr(wechat_ds, "chat_with_provider", lambda **kw: {"content": '{"respond":true}'})
    responses = iter([delegate("执行一次"), reply("完成")])
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: next(responses))
    executed = []
    monkeypatch.setattr(steward, "run_agent", lambda **kw: executed.append(kw) or {"ok": True, "text": "完成"})
    real_delivery = wechat_ds.deliver_reply
    monkeypatch.setattr(wechat_ds, "deliver_reply", lambda *a, **kw: "stale")
    assert not wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    monkeypatch.setattr(wechat_ds, "deliver_reply", real_delivery)
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    assert len(executed) == 1


def test_quiet_credential_note_is_retained_for_later_ds_context(setup, monkeypatch, tmp_path):
    from backend.core.messaging import wechat_ds
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.tests.test_wechat_agent import ingest, message, HOOK
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path / "events.sqlite")
    store.ensure_hook(**{k: HOOK[k] for k in ("key", "account_id", "chat_id")})
    service = SimpleNamespace(store=store, config={}, workers=[], last_error=None,
        stop_event=threading.Event(), poll_once=lambda: None, sender=lambda *args, **kwargs: {})
    decisions = iter([False, True])
    monkeypatch.setattr(wechat_ds, "chat_with_provider", lambda **kw: {"content": json.dumps({"respond": next(decisions)})})
    ingest(store, message(1, "密码original-secret", sender=OWNER_ACCOUNT))
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    def ds(**kwargs):
        assert "original-secret" in kwargs["messages"][-1]["content"]
        return reply("明白，已参考你前面提供的信息。")
    monkeypatch.setattr(steward, "chat_with_provider", ds)
    ingest(store, message(2, "记住了吗", sender=OWNER_ACCOUNT))
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    trusted = store.get_meta("ds_steward:test")
    assert not trusted.get("background")
    assert privacy.handoff(trusted["conversation"]["privacy_session"])["variables"]


def test_new_correction_retires_old_reply_without_replaying_cc(setup, monkeypatch, tmp_path):
    from backend.core.messaging import wechat_ds
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT
    from backend.tests.test_wechat_agent import ingest, message, HOOK
    from backend.core.messaging.wechat_agent_store import AgentStore
    store = AgentStore(tmp_path / "events.sqlite")
    store.ensure_hook(**{k: HOOK[k] for k in ("key", "account_id", "chat_id")})
    ingest(store, message(1, "处理任务", sender=OWNER_ACCOUNT))
    sent, executed = [], []
    added = []
    def poll():
        if not added:
            ingest(store, message(2, "补充一条新要求", sender=OWNER_ACCOUNT))
            added.append(True)
    service = SimpleNamespace(store=store, config={}, workers=[], last_error=None,
        stop_event=threading.Event(), poll_once=poll,
        sender=lambda *args, **kwargs: sent.append(args) or {})
    monkeypatch.setattr(wechat_ds, "chat_with_provider", lambda **kw: {"content": '{"respond":true}'})
    responses = iter([delegate("执行一次"), reply("旧答复"), reply("已收到新要求")])
    monkeypatch.setattr(steward, "chat_with_provider", lambda **kw: next(responses))
    monkeypatch.setattr(steward, "run_agent", lambda **kw: executed.append(kw) or {"ok": True, "text": "完成"})
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    assert not sent and len(executed) == 1
    assert wechat_ds.process_ds_hook(service, {**HOOK, "processor": "ds"})
    assert len(sent) == 1 and len(executed) == 1
