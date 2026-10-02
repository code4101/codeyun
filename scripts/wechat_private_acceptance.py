"""Real owner-to-AI private bridge: no @, plain output and reused daily thread."""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # noqa: F401
    from backend.core.codex.wechat_agent import CodexWechatClient
    from backend.core.messaging.wechat_agent import WechatAgentService, ATTENDANCE_ACCOUNT, OWNER_ACCOUNT
    from backend.core.messaging.wechat_agent_store import AgentStore
    from backend.core.temp_paths import codeyun_temp_root
    from pyxllib.autogui.wechat_accounts import check_account, get_account_storage
    from pyxllib.autogui.weixin4_instrumentation import send_text

    token = str(int(time.time()))
    root = codeyun_temp_root("wechat-private-acceptance") / token
    root.mkdir(parents=True, exist_ok=True)
    evidence = {"token": token, "turns": []}
    for account, peer in ((OWNER_ACCOUNT, ATTENDANCE_ACCOUNT), (ATTENDANCE_ACCOUNT, OWNER_ACCOUNT)):
        assert check_account(account, [peer])["recipients"][peer] == peer
    hook = {"key": "owner-private-test", "name": "代号4101", "account_id": ATTENDANCE_ACCOUNT,
            "chat_id": OWNER_ACCOUNT, "kind": "owner_private", "followup_seconds": 0}
    store = AgentStore(root / "events.sqlite")
    service = WechatAgentService({"accounts": [ATTENDANCE_ACCOUNT], "hooks": [hook]}, store=store)
    client = CodexWechatClient()
    confirmed = set()
    try:
        service.poll_once()
        questions = [f"私聊桥接联调 {token}。请实际运行 Python 算两个事项：7/66的百分比保留一位小数，"
                     "以及总人数为0时安全返回0.0。不要改任何业务数据。只用自然语言简短回复，并带上联调编号。",
                     f"私聊桥接联调 {token} 补充：刚才是哪两个计算结果？沿用当前上下文，只回一行并带上编号。"]
        for question in questions:
            send_text(ATTENDANCE_ACCOUNT, question, sender_account_id=OWNER_ACCOUNT)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                service.poll_once()
                if service.process_hook(hook, client):
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Owner private message not consumed")
            sent = sorted((r for r in store.status()["outbox"] if r["status"] == "sent"), key=lambda r: r["id"])
            reply = sent[-1]
            assert token in reply["text"] and "10.6" in reply["text"] and "0.0" in reply["text"]
            assert not reply["text"].startswith('{"action"')
            copies = []
            for account, peer in ((ATTENDANCE_ACCOUNT, OWNER_ACCOUNT), (OWNER_ACCOUNT, ATTENDANCE_ACCOUNT)):
                storage = get_account_storage(account)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    storage.poll_updates()
                    rows = storage.list_messages(peer, limit=12, include_resources=False)["items"]
                    matches = [m for m in rows if m.get("sender_username") == ATTENDANCE_ACCOUNT
                               and m.get("message_text", "").endswith(reply["text"])
                               and m["create_time"] >= int(reply["created_at"]) and m.get("server_id")
                               and str(m["server_id"]) not in confirmed]
                    if matches:
                        break
                    time.sleep(1)
                else:
                    raise AssertionError("No actual private reply visible")
                copies.append({"account_id": account, "server_id": str(matches[0]["server_id"])})
            assert copies[0]["server_id"] == copies[1]["server_id"]
            confirmed.add(copies[0]["server_id"])
            evidence["turns"].append({"thread_id": store.hook(hook["key"])["thread_id"], "model": client.model,
                                      "reply": reply["text"], "readback": copies})
            print(json.dumps(evidence["turns"][-1], ensure_ascii=True), flush=True)
        assert len(confirmed) == 2
        assert evidence["turns"][0]["thread_id"] == evidence["turns"][1]["thread_id"]
        evidence["verified"] = True
    finally:
        client.close()
        evidence["status"] = store.status()
        output = root / "evidence.json"
        output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"evidence_path": str(output)}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
