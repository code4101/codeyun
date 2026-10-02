"""Operate the account listener through its public business interface."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core.attendance.independent_engine_adapter import ensure_attendance_engine_importable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    configure = sub.add_parser("configure", help="Set explicit account and hook; enabled service takes effect on next start")
    configure.add_argument("--chat", choices=["test", "production"], required=True)
    configure.add_argument("--enabled", action="store_true")
    configure.add_argument("--text-mentions", action="store_true", help="Also accept exact leading @代号4102 text (including old nickname)")
    sub.add_parser("run", help="Run one durable service; Ctrl+C stops its workers")
    sub.add_parser("status")
    poll = sub.add_parser("poll", help="Refresh without running agents; baseline does not replay history")
    poll.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    ensure_attendance_engine_importable()
    import xlproject.loadenv  # All live account/environment access uses the project loader.
    from backend.core.messaging.wechat_agent import (
        ATTENDANCE_ACCOUNT, ATTENDANCE_MENTION_ALIASES, TEST_CHAT, PRODUCTION_CHAT, WechatAgentService,
        load_config, save_config, wechat_agent_status,
    )
    if args.command == "configure":
        from pyxllib.autogui.wechat_accounts import check_account
        name = "考勤后台" if args.chat == "test" else "考勤中台"
        expected = TEST_CHAT if args.chat == "test" else PRODUCTION_CHAT
        check = check_account(ATTENDANCE_ACCOUNT, [name])
        if check["recipients"][name] != expected:
            raise ValueError("Group identity changed; refusing to configure")
        config = save_config({"enabled": args.enabled, "poll_seconds": 5, "accounts": [ATTENDANCE_ACCOUNT],
                              "hooks": [{"key": "attendance-" + args.chat, "name": name, "account_id": ATTENDANCE_ACCOUNT,
                                         "chat_id": expected, "followup_seconds": 0,
                                         "mention_ids": [ATTENDANCE_ACCOUNT],
                                         "mention_aliases": ATTENDANCE_MENTION_ALIASES if args.text_mentions else []}]})
        print(json.dumps(config, ensure_ascii=False))
    elif args.command == "status":
        print(json.dumps(wechat_agent_status(), ensure_ascii=False))
    elif args.command == "poll":
        service = WechatAgentService(load_config())
        for _ in range(args.samples):
            print(json.dumps(service.poll_once(), ensure_ascii=False), flush=True)
        print(json.dumps(service.status(), ensure_ascii=False), flush=True)
    else:
        config = load_config()
        if not config.get("enabled"):
            raise ValueError("Service disabled; configure --enabled first")
        service = WechatAgentService(config)
        try:
            service.start()
            print(json.dumps({"started": True}, ensure_ascii=False), flush=True)
            while not service.stop_event.wait(5):
                # Observable heartbeat; no model call or group message for idle polls.
                service.store.set_meta("service", {"pid": __import__("os").getpid(), "heartbeat": time.time()})
        except KeyboardInterrupt:
            pass
        finally:
            service.stop()


if __name__ == "__main__":
    main()
