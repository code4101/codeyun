"""Account listener -> durable events -> group hooks -> Codex -> guarded reply.

Five seconds is a check interval, not a promise of model response latency.
Polling and model execution are separate workers. Hook filtering is structural;
the model decides relevance, question ownership and whether a reply is warranted.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import statistics
import threading
import time

from filelock import FileLock, Timeout

from backend.core.codex.wechat_agent import CodexWechatClient
from backend.core.messaging.wechat_agent_store import AgentStore, SHANGHAI, should_roll_session
from backend.core.settings import ROOT_DIR, get_settings

logger = logging.getLogger(__name__)
ATTENDANCE_ACCOUNT = "wxid_gxgjjgft1oj722"
TEST_CHAT = "51653518650@chatroom"
PRODUCTION_CHAT = "52281119334@chatroom"


def agent_root() -> Path:
    return get_settings().data_dir / "wechat-agent"


def load_config() -> dict:
    path = agent_root() / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"enabled": False, "accounts": [], "hooks": []}


def save_config(config: dict) -> dict:
    """Persist explicit account/group identities; names cannot expand routing."""
    interval = float(config.get("poll_seconds", 5))
    if not 1 <= interval <= 300:
        raise ValueError("poll_seconds must be 1..300")
    accounts = config.get("accounts") or []
    if not accounts or len(accounts) != len(set(accounts)) or any(not re.fullmatch(r"wxid_[A-Za-z0-9]+", x) for x in accounts):
        raise ValueError("Unique explicit account IDs are required")
    keys = set()
    for hook in config.get("hooks") or []:
        if hook["key"] in keys or hook["account_id"] not in accounts or not re.fullmatch(r"\d+@chatroom", hook["chat_id"]):
            raise ValueError("Invalid hook identity")
        if hook["account_id"] != ATTENDANCE_ACCOUNT:
            raise ValueError("Attendance replies must use 考勤返款")
        keys.add(hook["key"])
    path = agent_root() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=15):
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
    return config


def is_hard(event: dict, hook: dict) -> bool:
    if event.get("sender_id") == hook["account_id"]:
        return False
    if set(event.get("mentions") or []) & set(hook.get("mention_ids") or [hook["account_id"]]):
        return True
    # Optional exact leading textual @ compatibility, never substring matching.
    # Native pure-text send cannot emit structured @ metadata; this also permits
    # API-only tests without switching to the WeChat GUI.
    text = event.get("text", "")
    sender = event.get("sender_id", "")
    if sender and text.startswith(sender + ":\n"):
        text = text[len(sender) + 2:]
    for alias in hook.get("mention_aliases") or []:
        if re.match(r"^@" + re.escape(alias) + r"(?:\s|[，,:：]|$)", text):
            return True
    return False


class WechatAgentService:
    def __init__(self, config: dict, *, store: AgentStore | None = None, source_factory=None, client_factory=CodexWechatClient, sender=None):
        self.config = config
        self.store = store or AgentStore(agent_root() / "events.sqlite")
        self.source_factory = source_factory
        self.client_factory = client_factory
        self.sender = sender
        self.stop_event = threading.Event()
        self.poll_lock = threading.Lock()
        self.workers = []
        self.clients = []
        self.sources = {}
        self.samples = deque(maxlen=120)
        self.last_error = None
        self.last_poll_at = None
        self.owner_lock = None
        for hook in config.get("hooks") or []:
            self.store.ensure_hook(hook["key"], hook["account_id"], hook["chat_id"])

    def source(self, account: str):
        if account not in self.sources:
            if self.source_factory is None:
                from pyxllib.autogui.wechat_accounts import get_account_storage
                self.sources[account] = get_account_storage(account)
            else:
                self.sources[account] = self.source_factory(account)
        return self.sources[account]

    def poll_once(self) -> dict:
        """Only one account sweep at a time, including the final reply sweep."""
        with self.poll_lock:
            started = time.perf_counter()
            counts = {}
            for account in self.config["accounts"]:
                batch = self.source(account).poll_updates(self.store.get_meta("cursor:" + account))
                counts[account] = self.store.ingest(account, batch)
                # Drain bounded pages before accepting a reply, not only one page.
                while batch.get("has_more") and not self.stop_event.is_set():
                    batch = self.source(account).poll_updates(batch["cursor"])
                    counts[account] += self.store.ingest(account, batch)
            elapsed = time.perf_counter() - started
            self.samples.append(elapsed)
            self.last_poll_at = time.time()
            self.last_error = None
            self.store.set_meta("listener", {"last_poll_at": self.last_poll_at, "elapsed_seconds": elapsed, "new_events": counts})
            return {"elapsed_seconds": elapsed, "new_events": counts}

    def start(self):
        if self.workers:
            return
        lock = FileLock(str(self.store.path.with_suffix(".owner.lock")), timeout=0)
        lock.acquire()
        self.owner_lock = lock
        try:
            self.store.recover()
            self.store.set_meta("service", {"pid": os.getpid(), "heartbeat": time.time(), "stopped": False})
            try:
                self.poll_once()
            except Exception as exc:
                # Stay observable and retry online discovery; agents stay gated
                # until a successful account sweep establishes freshness.
                self.last_error = str(exc)
                logger.warning("WeChat listener starts in degraded state: %s", exc)
            specs = [(self._poll_loop, (), "wechat-account-listener")]
            specs += [(self._hook_loop, (hook,), "wechat-hook-" + hook["key"]) for hook in self.config.get("hooks") or []]
            for target, args, name in specs:
                thread = threading.Thread(target=target, args=args, name=name, daemon=True)
                thread.start()
                self.workers.append(thread)
        except BaseException:
            lock.release()
            self.owner_lock = None
            raise

    def _poll_loop(self):
        failures = 0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
                failures = 0
            except Exception as exc:
                failures += 1
                self.last_error = str(exc)
                self.store.set_meta("listener_error", {"at": time.time(), "error": str(exc)})
                logger.warning("WeChat listener: %s", exc)
            self.store.set_meta("service", {"pid": os.getpid(), "heartbeat": time.time(), "stopped": False})
            interval = min(60, self.config.get("poll_seconds", 5) * 2 ** min(failures, 4))
            self.stop_event.wait(max(0.1, interval - (time.monotonic() - started)))

    def _hook_loop(self, hook: dict):
        client = self.client_factory()
        self.clients.append(client)
        while not self.stop_event.is_set():
            try:
                self.process_hook(hook, client)
            except Exception as exc:
                self.store.update_hook(hook["key"], status="failed", last_error=str(exc), turn_id=None,
                                       retry_at=time.time() + 30)
                logger.exception("WeChat hook %s failed", hook["key"])
                client.close()
                client = self.client_factory()
                self.clients.append(client)
            self.stop_event.wait(0.5)
        client.close()

    def process_hook(self, hook: dict, client) -> bool:
        key = hook["key"]
        state = self.store.hook(key)
        if self.workers and (self.last_poll_at is None or self.last_error):
            return False
        if state["status"] == "uncertain" or state["retry_at"] > time.time():
            return False
        messages = self.store.messages(key, state["consumed_seq"])
        if not messages:
            return False
        hard = [m for m in messages if is_hard(m, hook)]
        followup_seconds = hook.get("followup_seconds", 0)
        if not hard and (not state["thread_id"] or (followup_seconds > 0 and state["awake_until"] < time.time())):
            self.store.update_hook(key, consumed_seq=messages[-1]["seq"])
            return False
        if hard:
            self.store.update_hook(key, awake_until=time.time() + hook.get("followup_seconds", 1800))
        first_time = hard[0]["timestamp"] if hard else messages[0]["timestamp"]
        boundary = bool(state["thread_id"]) and should_roll_session(state["session_day"], state["last_question_at"], first_time)
        thread_id = state["thread_id"]
        day = datetime.fromtimestamp(first_time, SHANGHAI).date().isoformat() if not thread_id else state["session_day"]
        thread_id = client.open_thread(thread_id, f"考勤群 {hook.get('name', hook['chat_id'])} {day}")
        self.store.update_hook(key, thread_id=thread_id, session_day=day, status="running", last_error=None, retry_at=0)
        if boundary:
            # Ordinary late followups and @ greetings need semantic routing too.
            # Classify in the existing thread before creating a new daily thread;
            # no business tools or effects are allowed during this routing turn.
            routed_through = messages[-1]["seq"]
            routing_prompt = json.dumps({"task": "跨天会话路由：仅判断新增消息是否属于需要你处理的有效考勤问题或补充/纠正。"
                                         "禁止使用工具、查询数据、修改业务或发送消息。相关消息填question_seqs，action=defer；"
                                         "无关消息action=ignore，question_seqs为空；text始终为空。summary保留未结问题。",
                                         "previous_summary": state["summary"], "new_messages": messages,
                                         "context": self.store.recent(key, routed_through)}, ensure_ascii=False)
            routing = client.run(thread_id, routing_prompt,
                                 lambda: any(is_hard(m, hook) for m in self.store.messages(key, routed_through)),
                                 self.stop_event, lambda turn: self.store.update_hook(key, turn_id=turn))
            if routing.get("interrupted"):
                self.store.update_hook(key, status="idle", turn_id=None)
                return False
            if not set(routing.get("question_seqs") or []) & {m["seq"] for m in messages}:
                self.store.update_hook(key, consumed_seq=routed_through, status="idle", turn_id=None)
                return True
            day = datetime.fromtimestamp(first_time, SHANGHAI).date().isoformat()
            state["summary"] = routing.get("summary") or state["summary"]
            thread_id = client.open_thread(None, f"考勤群 {hook.get('name', hook['chat_id'])} {day}")
            self.store.update_hook(key, thread_id=thread_id, session_day=day, summary=state["summary"])
        candidate = None
        reviewed_seq = state["consumed_seq"]
        last_question_at = state["last_question_at"]
        while not self.stop_event.is_set():
            messages = self.store.messages(key, reviewed_seq)
            if not messages:
                self.store.update_hook(key, status="idle", turn_id=None)
                return False
            through = messages[-1]["seq"]
            covered = {"seq": through, "pending": [], "events": list(messages)}
            context = self.store.recent(key, through, limit=30)
            prompt = json.dumps({"task": "回复前复查" if candidate else "处理群消息",
                                 "account_id": hook["account_id"], "chat_id": hook["chat_id"],
                                 "previous_summary": state["summary"], "previous_unsent_candidate": candidate,
                                 "delivery_receipts": self.store.deliveries(key),
                                 "context": context, "new_messages": messages,
                                 "hard_event_seqs": [m["seq"] for m in messages if is_hard(m, hook)],
                                 "instructions": "仅判断并处理属于你的考勤问题。无需回复时忽略；候选回复由工程发送。"}, ensure_ascii=False)

            def hard_interrupt():
                return any(is_hard(m, hook) for m in self.store.messages(key, through))

            def on_start(turn_id):
                self.store.update_hook(key, turn_id=turn_id, status="running")

            def soft_updates(accepted):
                if accepted:
                    if covered["pending"]:
                        covered["seq"] = covered["pending"][-1]["seq"]
                        covered["events"].extend(covered["pending"])
                    return None
                pending = self.store.messages(key, covered["seq"])
                if any(is_hard(m, hook) for m in pending):
                    return []
                covered["pending"] = pending
                return pending

            result = client.run(thread_id, prompt, hard_interrupt, self.stop_event, on_start, soft_updates=soft_updates)
            if result.get("interrupted"):
                # Already supplied history stays in this daily thread. The next
                # prompt focuses on arrivals that forced cancellation.
                reviewed_seq = through
                continue
            if self.stop_event.is_set():
                return False
            if (candidate and candidate["action"] == "reply" and result["action"] == "ignore"
                    and not result.get("question_seqs")):
                # Unrelated chatter cannot erase an unsent business answer.
                result = {**candidate, "summary": result.get("summary", candidate.get("summary", ""))}
            candidate = result
            through = covered["seq"]
            messages = covered["events"]
            questions = set(candidate.get("question_seqs") or [])
            relevant = [m for m in messages if m["seq"] in questions]
            if relevant:
                last_question_at = max(last_question_at or 0, max(m["timestamp"] for m in relevant))
            reviewed_seq = through
            # Explicit live refresh closes the gap between periodic polls.
            self.poll_once()
            if self.store.messages(key, through):
                continue
            updates = {"summary": candidate.get("summary", "")}
            if last_question_at is not None:
                updates["last_question_at"] = last_question_at
            if relevant:
                updates["awake_until"] = time.time() + hook.get("followup_seconds", 1800)
            self.store.update_hook(key, **updates)
            if candidate["action"] != "reply":
                self.store.update_hook(key, consumed_seq=through, status="idle", turn_id=None)
                return True
            text = candidate["text"].strip()
            reply_id = self.store.reserve_reply(key, through, text)
            if reply_id is None:
                if self.store.messages(key, through):
                    continue
                raise RuntimeError("Reply already reserved; delivery must be reconciled")
            try:
                sender = self.sender
                if sender is None:
                    from pyxllib.autogui.weixin4_instrumentation import send_text
                    sender = send_text
                sent = sender(hook["chat_id"], text, sender_account_id=hook["account_id"])
            except Exception as exc:
                self.store.finish_reply(reply_id, "uncertain", {"error": str(exc)})
                return False
            self.store.finish_reply(reply_id, "sent", sent)
            return True
        self.store.update_hook(key, status="idle", turn_id=None)
        return False

    def status(self) -> dict:
        return {"running": bool(self.workers) and not self.stop_event.is_set(), "last_poll_at": self.last_poll_at,
                "last_error": self.last_error, "sample_count": len(self.samples),
                "poll_mean_seconds": statistics.mean(self.samples) if self.samples else None,
                "poll_std_seconds": statistics.stdev(self.samples) if len(self.samples) > 1 else 0,
                **self.store.status()}

    def stop(self):
        self.stop_event.set()
        for thread in self.workers:
            thread.join(timeout=5)
        for client in self.clients:
            client.close()
        for thread in self.workers:
            thread.join(timeout=2)
        if any(thread.is_alive() for thread in self.workers):
            raise RuntimeError("WeChat workers still own the service; cannot release single-instance lock")
        if self.owner_lock:
            self.store.set_meta("service", {"pid": os.getpid(), "heartbeat": time.time(), "stopped": True})
            self.owner_lock.release()
            self.owner_lock = None


_service: WechatAgentService | None = None
_service_lock = threading.Lock()


def start_wechat_agent_service() -> dict:
    global _service
    with _service_lock:
        config = load_config()
        if not config.get("enabled"):
            return {"running": False, "reason": "disabled"}
        if _service is None:
            from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
            ensure_attendance_engine_importable()
            import xlproject.loadenv  # noqa: F401
            service = WechatAgentService(config)
            service.start()
            _service = service
        return _service.status()


def stop_wechat_agent_service() -> dict:
    global _service
    with _service_lock:
        if _service:
            _service.stop()
            _service = None
        return {"running": False}


def wechat_agent_status() -> dict:
    if _service:
        return _service.status()
    store = AgentStore(agent_root() / "events.sqlite")
    heartbeat = store.get_meta("service", {})
    import psutil
    running = (not heartbeat.get("stopped", True) and time.time() - heartbeat.get("heartbeat", 0) < 75
               and psutil.pid_exists(heartbeat.get("pid", -1)))
    return {"running": running, "external_worker": heartbeat,
            "listener": store.get_meta("listener"), **store.status()}
