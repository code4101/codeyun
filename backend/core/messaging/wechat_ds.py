"""WeChat admission and guarded delivery around the generic official DS steward."""
import json
import time
from uuid import uuid4

from backend.core.ai.chat import chat_with_provider
from backend.core.ai.steward import respond
from backend.core.messaging.wechat_delivery import prepare_reply, deliver_reply


class DsWechatClient:
    """No external agent session is constructed for the DS conversational owner."""
    def close(self):
        pass


def process_ds_hook(service, hook):
    from backend.core.messaging.wechat_agent import OWNER_ACCOUNT, model_message
    key = hook["key"]
    state = service.store.hook(key)
    if (service.stop_event.is_set() or state["retry_at"] > time.time()
            or state["status"] == "uncertain" or service.store.uncertain_replies(key)
            or service.workers and (service.last_poll_at is None or service.last_error)):
        return False
    messages = service.store.pending_messages(key, state["consumed_seq"])
    if not messages:
        return False
    through = messages[-1]["seq"]
    name = "ds_steward:" + key
    trusted = service.store.get_meta(name) or {"thread_id": "ds:" + uuid4().hex}
    pending = trusted.get("pending")
    if pending and pending["through"] != through:
        # Finish the durable old request before admitting a newer one. Delivery
        # will reject stale replies; the next turn then sees the new correction.
        messages = [m for m in messages if m["seq"] <= pending["through"]]
        through = pending["through"]
    if not messages:
        trusted.pop("pending", None)
        service.store.set_meta(name, trusted)
        return False
    if not pending:
        # Qwen decides admission only; no local-model answer is delivered.
        text = json.dumps([model_message(m) for m in messages], ensure_ascii=False)
        try:
            decision = chat_with_provider(provider_id="ollama", model="qwen3.5:4b-instruct", thinking=False,
                system_prompt="这是用户与通用管家的专用群，通常类似私聊。仅判断现在是否需要回复。明确提问、指令、追问、招呼都回复；纯系统通知或无人类意图的内容不回复。仅返回JSON：{\"respond\":true/false}，禁止回答正文。",
                messages=[{"role": "user", "content": text}], response_format="json", timeout_seconds=30)
            engage = json.loads(decision["content"])["respond"]
            if type(engage) is not bool:
                raise ValueError()
        except Exception:
            engage = True  # A classifier outage must not lose a human request.
        if not engage:
            # "No reply now" is not "forget this information". Credential
            # notes and quiet context must reach DS when a later request refers
            # to them; they are never forwarded directly to CC.
            trusted.setdefault("background", {})[str(through)] = text
            service.store.set_meta(name, trusted)
            service.store.update_hook(key, consumed_seq=through, status="idle", turn_id=None)
            return True
        background = trusted.pop("background", {})
        if background:
            text = "此前无需回复的消息（仅作上下文）：\n" + "\n".join(background.values()) + "\n当前消息：\n" + text
        pending = {"through": through, "input": text,
                   "allow_execution": all(m["sender_id"] == OWNER_ACCOUNT for m in messages)}
        trusted["pending"] = pending
        service.store.set_meta(name, trusted)
    def checkpoint(_):
        service.store.set_meta(name, trusted)
    service.store.update_hook(key, thread_id=trusted["thread_id"], status="running", last_error=None)
    answer = respond(trusted.setdefault("conversation", {}), pending["input"], request_id=str(through),
                     checkpoint=checkpoint, allow_execution=pending["allow_execution"], stop_event=service.stop_event)
    service.poll_once()
    if service.store.pending_messages(key, through):
        # Preserve DS context, but retire the obsolete answer. Never replay CC.
        service.store.update_hook(key, consumed_seq=through)
        trusted.pop("pending", None)
        checkpoint(trusted)
        service.store.update_hook(key, status="idle", turn_id=None)
        return True
    plan = prepare_reply(answer, messages, private=False, limit=service.config.get("reply_max_chars", 800))
    outcome = deliver_reply(service.store, hook, through, answer, plan,
                            sender=service.sender, stop_event=service.stop_event)
    if outcome == "sent":
        trusted.pop("pending", None)
        checkpoint(trusted)
        service.store.update_hook(key, status="idle", turn_id=None, last_error=None, retry_at=0)
        return True
    return False
