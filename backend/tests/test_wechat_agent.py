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


class PlainClient:
    def __init__(self, text="直接回复"):
        self.text, self.prompts, self.opened = text, [], []
    def open_thread(self, thread_id, title, **kwargs):
        self.opened.append((thread_id, title, kwargs))
        return thread_id or f"private-{len(self.opened)}"
    def background(self, thread_id):
        return "旧会话中的未结问题，仅供参考"
    def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
        assert kwargs["raw_text"] is True
        self.prompts.append(prompt)
        on_start("private-turn")
        return {"action": "reply", "text": self.text, "summary": self.text, "question_seqs": []}


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
