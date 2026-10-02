"""Real, bounded two-account acceptance in the explicitly authorized test group.

Uses leading text @ compatibility because the native send API only sends plain
text. Structured @ metadata is checked separately from real archived messages.
Never targets the production group or changes attendance/refund data.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable


def main():
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # noqa: F401
    from backend.core.codex.wechat_agent import CodexWechatClient
    from backend.core.messaging.wechat_agent import WechatAgentService, ATTENDANCE_ACCOUNT, TEST_CHAT
    from backend.core.messaging.wechat_agent_store import AgentStore
    from backend.core.temp_paths import codeyun_temp_root
    from pyxllib.autogui.weixin4_instrumentation import send_text
    from pyxllib.autogui.wechat_accounts import check_account

    for account in (ATTENDANCE_ACCOUNT, "wxid_m1cd4f5aahut22"):
        if check_account(account, ["考勤后台"])["recipients"]["考勤后台"] != TEST_CHAT:
            raise ValueError("Test group identity mismatch")
    token = str(int(time.time()))
    root = codeyun_temp_root("wechat-agent") / token
    root.mkdir(parents=True, exist_ok=True)
    store = AgentStore(root / "acceptance.sqlite")
    trace = []
    import threading
    candidate_ready, release_candidate = threading.Event(), threading.Event()
    gate = {"enabled": False}

    def emit(kind, **values):
        event = {"at": time.time(), "kind": kind, **values}
        trace.append(event)
        print(json.dumps(event, ensure_ascii=False), flush=True)

    class AuditedClient(CodexWechatClient):
        def run(self, thread_id, prompt, hard_interrupt, stop_event, on_start, **kwargs):
            def started(turn_id):
                on_start(turn_id)
                emit("turn_started", thread_id=thread_id, turn_id=turn_id)
            result = super().run(thread_id, prompt, hard_interrupt, stop_event, started, **kwargs)
            emit("turn_finished", thread_id=thread_id, action=result.get("action"), interrupted=result.get("interrupted", False))
            if gate["enabled"] and result.get("action") == "reply":
                gate["enabled"] = False
                candidate_ready.set()
                if not release_candidate.wait(60):
                    raise TimeoutError("Reply-check acceptance barrier timed out")
            return result

    hook = {"key": "acceptance", "name": "考勤后台联调", "account_id": ATTENDANCE_ACCOUNT,
            "chat_id": TEST_CHAT, "mention_ids": [ATTENDANCE_ACCOUNT], "mention_aliases": ["考勤返款"], "followup_seconds": 1800}
    config = {"accounts": [ATTENDANCE_ACCOUNT], "poll_seconds": 5, "hooks": [hook]}
    service = WechatAgentService(config, store=store, client_factory=AuditedClient)

    def send(text):
        result = send_text(TEST_CHAT, f"[Agent联调 {token}] {text}" if not text.startswith("@") else text,
                           sender_account_id="wxid_m1cd4f5aahut22")
        emit("test_input_sent", result=result["result"]["result"])

    def wait_until(predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            state = store.hook("acceptance")
            if state["status"] in {"failed", "uncertain"}:
                raise RuntimeError(f"Agent failed: {state['last_error']}")
            time.sleep(1)
        raise TimeoutError("Acceptance condition exceeded deadline")

    def sent_count():
        return sum(row["status"] == "sent" for row in store.status()["outbox"])

    try:
        service.start()
        emit("listener_started", evidence_dir=str(root))
        send(f"@考勤返款\u2005[Agent联调 {token}] 这是工程联调，不查询或修改任何考勤数据。"
             "请先等待我补充（可用一次有限的35秒等待），再拟一句测试回复，初版标记为第一版。")
        wait_until(lambda: store.hook("acceptance")["status"] == "running", timeout=60)
        send("补充同一个联调问题：初版作废，最终回复应采用第二版；这条不带@。")
        send(f"@考勤返款\u2005[Agent联调 {token}] 更正：不需要继续等待。最终只回复“联调完成：已采用第二版”。")
        wait_until(lambda: sent_count() >= 1)
        first_thread = store.hook("acceptance")["thread_id"]
        first_replies = [row["text"] for row in store.status()["outbox"] if row["status"] == "sent"]
        assert any("第二版" in text for text in first_replies), first_replies
        assert not any("第一版" in text for text in first_replies), first_replies
        emit("strong_and_soft_verified", replies=first_replies, thread_id=first_thread)
        send("补充刚才的同一个联调问题：请把测试结论改成第三版，只回复“联调完成：已采用第三版”。不涉及实际考勤数据。")
        wait_until(lambda: sent_count() >= 2)
        assert store.hook("acceptance")["thread_id"] == first_thread
        assert any("第三版" in row["text"] for row in store.status()["outbox"] if row["status"] == "sent")
        emit("unmentioned_followup_and_session_reuse_verified")
        gate["enabled"] = True
        send("继续同一个联调问题：请仅回复“联调完成：第四版初稿”。")
        wait_until(candidate_ready.is_set)
        send("更正同一个联调问题：第四版初稿作废，最终只回复“联调完成：已采用第五版”。")
        release_candidate.set()
        wait_until(lambda: sent_count() >= 3)
        texts = [row["text"] for row in store.status()["outbox"] if row["status"] == "sent"]
        assert any("第五版" in text for text in texts), texts
        assert not any("第四版" in text for text in texts), texts
        emit("pre_reply_live_refresh_verified")
        before = sent_count()
        previous_seq = max(m["seq"] for m in store.messages("acceptance"))
        send("这条是群友之间的无关闲聊：今天午饭吃面还是米饭？不需要AI回答。")
        wait_until(lambda: max(m["seq"] for m in store.messages("acceptance")) > previous_seq
                   and store.hook("acceptance")["status"] == "idle"
                   and store.hook("acceptance")["consumed_seq"] == max(m["seq"] for m in store.messages("acceptance")))
        assert sent_count() == before
        emit("poll_performance", samples=service.status()["sample_count"], mean=service.status()["poll_mean_seconds"], std=service.status()["poll_std_seconds"])
        service.stop()
        service = WechatAgentService(config, store=AgentStore(store.path), client_factory=AuditedClient)
        service.start()
        time.sleep(8)
        assert sent_count() == before
        emit("unrelated_chat_echo_restart_dedupe_verified")
        # Query real normalized outgoing messages to verify account and delivery.
        page = service.source(ATTENDANCE_ACCOUNT).list_messages(TEST_CHAT, limit=20, include_resources=False)
        replies = [{"sender_id": m["sender_username"], "text": m.get("message_text", "")}
                   for m in page["items"] if m.get("sender_username") == ATTENDANCE_ACCOUNT and "联调完成" in m.get("message_text", "")]
        assert any("第二版" in m["text"] for m in replies) and any("第三版" in m["text"] for m in replies), replies
        emit("actual_group_delivery_verified", replies=replies)
    finally:
        release_candidate.set()
        service.stop()
        evidence = {"trace": trace, "status": service.status()}
        (root / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        emit("stopped", evidence=str(root / "evidence.json"))


if __name__ == "__main__":
    main()
