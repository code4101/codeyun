"""Verify default-model task execution and a followup in 考勤后台.

Only modifies a deliberately broken script under the system temporary directory.
Uses real account archive polling, Codex tools, host delivery and both-account
readback. The production listener can remain running; this isolated hook uses
its own store and never acquires the production service's worker ownership.
"""
from pathlib import Path
import json
import runpy
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # noqa: F401
    from backend.core.codex.wechat_agent import CodexWechatClient
    from backend.core.messaging.wechat_agent import WechatAgentService, ATTENDANCE_ACCOUNT, TEST_CHAT
    from backend.core.messaging.wechat_agent_store import AgentStore
    from backend.core.temp_paths import codeyun_temp_root
    from pyxllib.autogui.wechat_accounts import check_account, get_account_storage
    from pyxllib.autogui.weixin4_instrumentation import send_text

    owner = "wxid_m1cd4f5aahut22"
    token = str(int(time.time()))
    root = codeyun_temp_root("wechat-agent-acceptance") / token
    root.mkdir(parents=True, exist_ok=True)
    target = root / "ratio.py"
    target.write_text("def empty_ratio(empty, total):\n    return round(100 * empty / total, 1)\n", encoding="utf-8")
    evidence = {"token": token, "target": str(target), "turns": []}
    for account in (owner, ATTENDANCE_ACCOUNT):
        assert check_account(account, ["考勤后台"])["recipients"]["考勤后台"] == TEST_CHAT
    hook = {"key": "task-acceptance", "name": "考勤后台任务联调", "account_id": ATTENDANCE_ACCOUNT,
            "chat_id": TEST_CHAT, "mention_ids": [ATTENDANCE_ACCOUNT], "mention_aliases": ["考勤返款"],
            "followup_seconds": 0}
    store = AgentStore(root / "events.sqlite")
    service = WechatAgentService({"accounts": [ATTENDANCE_ACCOUNT], "hooks": [hook]}, store=store)
    client = CodexWechatClient()
    confirmed_ids = set()
    try:
        service.poll_once()  # Establish baseline before the test question.
        questions = [f"@考勤返款 [任务联调 {token}] 请实际修复临时脚本 {target.as_posix()} 的 empty_ratio 函数："
                     "总人数为0时返回0.0，正常比例仍保留一位小数。执行验证0/0、7/66及0/66，完成后在群里简短报结果。"
                     "只修改此临时文件，不修改真实考勤数据；不要只给建议。",
                     f"[任务联调 {token} 补充] 请重新读取刚才修复的临时脚本，再实际运行一次验证7/66和0/0，简短汇报。"]
        for index, question in enumerate(questions):
            send_text(TEST_CHAT, question, sender_account_id=owner)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                service.poll_once()
                if service.process_hook(hook, client):
                    break
                time.sleep(1)
            else:
                raise TimeoutError("Test question did not reach the hook")
            sent = sorted((row for row in store.status()["outbox"] if row["status"] == "sent"), key=lambda row: row["id"])
            assert len(sent) == index + 1, store.status()
            ratio = runpy.run_path(str(target))["empty_ratio"]
            assert ratio(0, 0) == 0.0 and ratio(7, 66) == 10.6 and ratio(0, 66) == 0.0
            reply = sent[-1]["text"]
            copies = []
            for account in (ATTENDANCE_ACCOUNT, owner):
                storage = get_account_storage(account)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    storage.poll_updates()  # Native send queues delivery; archive confirmation arrives later.
                    page = storage.list_messages(TEST_CHAT, limit=15, include_resources=False)
                    matches = [row for row in page["items"] if row.get("sender_username") == ATTENDANCE_ACCOUNT
                               and row.get("message_text", "").endswith(reply) and row.get("server_id")
                               and row["create_time"] >= int(sent[-1]["created_at"])
                               and str(row["server_id"]) not in confirmed_ids]
                    if matches:
                        break
                    time.sleep(1)
                else:
                    raise AssertionError(f"No actual reply visible to {account}")
                copies.append({"account_id": account, "server_id": str(matches[-1]["server_id"])})
            assert copies[0]["server_id"] == copies[1]["server_id"]
            confirmed_ids.add(copies[0]["server_id"])
            evidence["turns"].append({"thread_id": store.hook(hook["key"])["thread_id"],
                                      "model": client.model, "reply": reply, "readback": copies})
            print(json.dumps(evidence["turns"][-1], ensure_ascii=True), flush=True)
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
