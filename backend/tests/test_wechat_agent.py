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


def test_polling_process_with_failed_active_hook_is_degraded():
    from backend.core.messaging.wechat_agent import describe_health
    state = {"running": True, "hooks": [{"key": "live", "status": "failed", "last_error": "configuration rejected"},
                                        {"key": "old-test", "status": "failed", "last_error": "unused"}]}
    result = describe_health(state, {"hooks": [{"key": "live"}]})
    assert result["health"] == "degraded" and result["errors"] == ["configuration rejected"]
    state["hooks"][0]["status"] = "idle"
    assert describe_health(state, {"hooks": [{"key": "live"}]})["health"] == "ready"


def test_daily_sessions_respect_midnight_and_two_hour_boundary():
    def stamp(text):
        return datetime.fromisoformat(text).replace(tzinfo=SHANGHAI).timestamp()
    last = stamp("2026-10-02T23:30:00")
    assert not should_roll_session("2026-10-02", last, stamp("2026-10-03T00:20:00"))
    assert not should_roll_session("2026-10-02", last, stamp("2026-10-03T01:29:59"))
    assert should_roll_session("2026-10-02", last, stamp("2026-10-03T01:30:00"))
    assert not should_roll_session("2026-10-03", stamp("2026-10-03T08:00:00"), stamp("2026-10-03T18:00:00"))


def test_cross_day_unmentioned_question_routes_before_new_daily_thread(store):
    ingest(store, message(1, "补充昨天的问题"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01", last_question_at=time.time() - 7201)
    hook = {**HOOK, "followup_seconds": 0}
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


def test_cross_day_unrelated_chatter_does_not_create_new_thread(store):
    ingest(store, message(1, "无关闲聊"))
    store.update_hook("test", thread_id="yesterday", session_day="2000-01-01", last_question_at=time.time() - 7201)
    class IgnoreClient(CandidateClient):
        def run(self, *args, **kwargs):
            return {"action": "ignore", "text": "", "summary": "", "question_seqs": []}
    assert service(store).process_hook({**HOOK, "followup_seconds": 0}, IgnoreClient())
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
    def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
        self.prompts.append(json.loads(prompt))
        on_start("turn")
        if self.callback:
            callback, self.callback = self.callback, None
            callback(hard_interrupt)
        return {"action": "reply", "text": "已纳入补充" if len(self.prompts) > 1 else "初稿", "summary": "未结摘要", "question_seqs": [1]}


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
    assert client.prompts[1]["previous_unsent_candidate"]["text"] == "初稿"
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
